"""core-return-sync — เติมฐานซากอะไหล่ (master_data.core_return_base) ด้วยบรรทัดเบิกใหม่จาก ATMS ทุกวัน.

พอร์ตกฎจากสคริปต์ในเครื่อง core-return-base (build_local / report_unreturned / load_base / pull)
แหล่ง: atms.stockmovement_v5 ย้อนหลัง CORE_RETURN_SYNC_MONTHS เดือน (ค่าเริ่มต้น 3 รวมเดือนนี้)
เขียน: upsert ตาม row_key ลง master_data.core_return_base — $set เฉพาะฟิลด์บรรยาย, $setOnInsert สถานะซาก
       ไม่ทับ coreStatus / ข้อมูลรับซาก / ติดตาม / รอบ · ไม่ลบอะไรเลย (แถวที่หายจาก ATMS แค่นับเป็น missing_in_atms)
       ข้ามบรรทัดที่ (wdCode, skuCode) มีแถว source "wms.store-withdraw" อยู่แล้ว
อ่านอย่างเดียวกับคอลเลกชันอื่น · ข้อมูลเดิมจากไฟล์ติดตาม (legacy) ไม่พอร์ต
Env: CORE_RETURN_SYNC_MONTHS (3) · CORE_RETURN_SYNC_DRY_RUN=1 (หรือส่ง {"dry_run": 1} ตอนยิง /pipeline/run → env DRY_RUN) = ไม่เขียน แค่นับ
"""
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pymongo import UpdateOne

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engineon"))

TARGET_DB, TARGET_COL = "master_data", "core_return_base"
SOURCE = "atms.stockmovement_v5"
WMS_SOURCE = "wms.store-withdraw"
DROP = ["_id", "max stock", "min stock", "downloaded_at", "row_hash", "source_row_no", "dup_seq", "หมายเหตุย่อย"]
PARTNER_FLAGS = ["รถร่วม", "รถร่วม พจน", "รถร่วมมีนา"]

EXCLUDE_PURPOSE = {
    "เบิกเพื่อทำความสะอาด", "PM-บำรุงรักษารถร่วม", "ยาง", "ค่ายางรถร่วม",
    "ค่าซ่อม/ค่าอะไหล่รถร่วม", "ค่าซ่อม/ค่าอะไหล่รถร่วมภายนอก(พจน.)", "คืนสินค้าซัพพลายเออร์",
}
REPAIR_PURPOSE = {
    "ซ่อม", "CM-งานซ่อม", "อะไหล่/วัสดุสิ้นเปลือง", "ซ่อมเคสอุบัติเหตุ", "AC-เคสอุบัติเหตุ",
    "PM ช่วงล่าง", "PM-บำรุงรักษา", "PM ความเเย็น", "แยคโม่", "แย็กโม่",
}
CAR_WAREHOUSES = {"คลังลาดกระบัง", "คลังสระบุรี", "คลัง DIST", "คลังขอนแก่น"}
CONSUMABLE_GROUPS = {"วัสดุสิ้นเปลือง", "ระบบวัสดุสิ้นเปลือง"}
BRANCH = {"คลังลาดกระบัง": "ลาดกระบัง", "คลังสระบุรี": "สระบุรี", "คลัง DIST": "DIST", "คลังขอนแก่น": "ขอนแก่น"}
OIL_RE = re.compile(r"^(น้ำมัน|จารบี|จาระบี)")
CODE_PREFIX_RE = re.compile(r"^\S+\s*:\s*")
MR_RE = re.compile(r"\b(?:[A-Z]{2,3}MR\d{7,8})\b")
PR_RE = re.compile(r"\b(?:[A-Z]{2,3}PR\d{7,8})\b")


def norm(p) -> str:
    return "".join(str(p).split())


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def month_list(n: int, now: datetime | None = None) -> list[str]:
    """n เดือนล่าสุดรวมเดือนนี้ (BKK) เรียงเก่า -> ใหม่ รูปแบบ YYYY-MM"""
    now = now or (datetime.now(timezone.utc) + timedelta(hours=7))
    y, m = now.year, now.month
    out = []
    for _ in range(max(1, n)):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


def apply_filters(rows: list[dict], partner_by_flag: dict[str, set], first_month: str | None = None) -> tuple[list[dict], list[tuple[str, int]]]:
    """กรองตามลำดับเดียวกับ build_local.py · คืน (แถวที่เหลือ, [(ชื่อขั้น, จำนวนที่เหลือ)])"""
    steps: list[tuple[str, int]] = []
    g = lambda r, k: r.get(k) if r.get(k) is not None else ""  # noqa: E731
    rows = [r for r in rows if (_num(r.get("จ่าย")) or 0) > 0 and (not first_month or str(r.get("year_month") or "") >= first_month)]
    steps.append(("จ่ายออก", len(rows)))
    rows = [r for r in rows if (_num(r.get("ยอดเงิน")) or 0) > 500]
    steps.append(("ยอดเงิน > 500", len(rows)))
    rows = [r for r in rows if not str(g(r, "กลุ่มสินค้า")).startswith("ค่าแรง")]
    steps.append(("ตัดกลุ่มค่าแรง", len(rows)))
    rows = [r for r in rows if str(g(r, "จุดประสงค์ในการเบิก")) not in EXCLUDE_PURPOSE
            and not str(g(r, "จุดประสงค์ในการเบิก")).startswith("T-")
            and "ร่วม" not in str(g(r, "จุดประสงค์ในการเบิก"))]
    steps.append(("ตัดจุดประสงค์ตามรายการ + รถร่วม", len(rows)))
    rows = [r for r in rows if str(g(r, "จุดประสงค์ในการเบิก")) in REPAIR_PURPOSE and str(g(r, "กลุ่มสินค้า")) != "น้ำมันเชื้อเพลิง"]
    steps.append(("เก็บเฉพาะงานซ่อม/บำรุงรักษา", len(rows)))
    for flag, plates in partner_by_flag.items():
        plates = {norm(p) for p in plates}
        rows = [r for r in rows if norm(g(r, "ทะเบียน")) not in plates]
        steps.append((f"ตัดทะเบียน {flag}", len(rows)))
    rows = [r for r in rows if r.get("คลังสินค้า") in CAR_WAREHOUSES]
    steps.append(("เก็บเฉพาะ 4 คลังรถ", len(rows)))
    rows = [r for r in rows if r.get("กลุ่มสินค้า") not in CONSUMABLE_GROUPS]
    steps.append(("ตัดกลุ่มวัสดุสิ้นเปลือง", len(rows)))
    rows = [r for r in rows if not OIL_RE.match(CODE_PREFIX_RE.sub("", str(g(r, "ชื่อสินค้า"))).strip())]
    steps.append(("ตัดหมวดน้ำมัน", len(rows)))
    return rows, steps


class GarageResolver:
    """หาอู่ของ MR ตามลำดับ: WMS repair_external > PO ค่าแรง > PO ของ MR (ร้านอะไหล่) > ไม่ทราบชื่อ > อู่ใน"""

    def __init__(self, repair_external: list[dict], purchase_requests: list[dict],
                 purchase_orders: list[dict], month_rows: list[dict]):
        self.rx = {r["mrNo"]: r.get("garage") for r in repair_external if r.get("mrNo") and r.get("garage")}
        self.po_by_pr = defaultdict(set)
        for p in purchase_orders:
            pr, sup = p.get("ใบขอสั่งซื้อ (PR)"), p.get("ซัพพลายเออร์")
            if pr and sup not in (None, "", "None"):
                self.po_by_pr[pr].add(str(sup).strip())
        self.pr_by_mr = defaultdict(set)
        for p in purchase_requests:
            for mr in MR_RE.findall(str(p.get("หมายเหตุ") or "")):
                self.pr_by_mr[mr].add(p.get("ใบขอสั่งซื้อ (PR)"))
        self.labour_prs = defaultdict(set)
        self.has_labour = set()
        for r in month_rows:
            if str(r.get("กลุ่มสินค้า") or "").startswith("ค่าแรง") and r.get("MR"):
                self.has_labour.add(r["MR"])
                for pr in PR_RE.findall(str(r.get("หมายเหตุ") or "")) + ([r["PR"]] if r.get("PR") not in (None, "", "None") else []):
                    self.labour_prs[r["MR"]].add(pr)

    def resolve(self, mr, warehouse) -> tuple[str, str, str]:
        if mr in self.rx:
            return "อู่นอก", self.rx[mr], "งานซ่อมอู่นอก (WMS)"
        sup = sorted({s for pr in self.labour_prs.get(mr, ()) for s in self.po_by_pr.get(pr, ())})
        if sup:
            return "อู่นอก", " / ".join(sup), "PO ค่าแรงของ MR"
        if mr in self.has_labour:
            sup2 = sorted({s for pr in self.pr_by_mr.get(mr, ()) for s in self.po_by_pr.get(pr, ())})
            return ("อู่นอก", " / ".join(sup2) if sup2 else "ไม่ทราบชื่ออู่",
                    "PO ของ MR (อาจเป็นร้านอะไหล่)" if sup2 else "มีค่าแรงแต่ไม่พบ PO")
        return "อู่ใน", f"อู่ใน {BRANCH.get(warehouse, warehouse)}", "ไม่มีค่าแรงใน MR"


def make_doc(r: dict, garage: tuple[str, str, str]) -> dict:
    return {
        "row_key": r["row_key"],
        "issuedDate": str(r.get("วันที่") or "")[:10],
        "yearMonth": r.get("year_month"),
        "warehouse": r.get("คลังสินค้า"),
        "wdCode": r.get("WD"),
        "mrCode": r.get("MR"),
        "purpose": r.get("จุดประสงค์ในการเบิก"),
        "skuCode": r.get("รหัสสินค้า"),
        "skuName": r.get("ชื่อสินค้า"),
        "productGroup": r.get("กลุ่มสินค้า"),
        "vehicleNo": r.get("เลขรถ"),
        "plate": r.get("ทะเบียน"),
        "driver": r.get("พจส."),
        "serialNo": r.get("เลขที่เฉพาะ"),
        "qtyIssued": _num(r.get("จ่าย")),
        "unitCost": _num(r.get("ราคาทุน")),
        "amount": _num(r.get("ยอดเงิน")),
        "remark": r.get("หมายเหตุ"),
        "source": SOURCE,
        "garageType": garage[0],
        "garage": garage[1],
        "garageSource": garage[2],
    }


def run(client, months: int = 3, dry_run: bool = False, sleep=time.sleep, now: datetime | None = None) -> dict:
    t0 = time.time()
    yms = month_list(months, now)
    atms, dw, md = client["atms"], client["datawarehouse"], client[TARGET_DB]
    col = md[TARGET_COL]

    raw: list[dict] = []
    for i, ym in enumerate(yms):
        if i:
            sleep(3)
        cur = (atms["stockmovement_v5"]
               .find({"year_month": ym, "จ่าย": {"$gt": 0}, "ยอดเงิน": {"$gt": 500}}, {f: 0 for f in DROP})
               .max_time_ms(60000).batch_size(2000))
        raw.extend(cur)
    fetched = len(raw)

    partner = {}
    for flag in PARTNER_FLAGS:
        partner[flag] = {p for p in dw["dw_stockmovement"].distinct(
            "ทะเบียน", {"month_year": {"$gte": yms[0]}, "partner_flag": flag}, maxTimeMS=60000) if p}
    rows, steps = apply_filters(raw, partner, yms[0])

    rx = list(md["repair_external"].find({"mrNo": {"$nin": [None, ""]}},
                                         {"_id": 0, "mrNo": 1, "garage": 1}).max_time_ms(60000).limit(60000))
    pr = list(atms["purchase_requests"].find({}, {"_id": 0, "ใบขอสั่งซื้อ (PR)": 1, "หมายเหตุ": 1}).max_time_ms(60000).batch_size(5000))
    po = list(atms["purchase_orders"].find({}, {"_id": 0, "ซัพพลายเออร์": 1, "ใบขอสั่งซื้อ (PR)": 1}).max_time_ms(60000).batch_size(5000))
    resolver = GarageResolver(rx, pr, po, raw)

    wms = {(r.get("wdCode"), r.get("skuCode")) for r in
           col.find({"source": WMS_SOURCE}, {"_id": 0, "wdCode": 1, "skuCode": 1}).max_time_ms(30000)}
    docs, skipped_wms, seen = [], 0, set()
    for r in rows:
        if not r.get("row_key") or r["row_key"] in seen:
            continue
        seen.add(r["row_key"])
        if (r.get("WD"), r.get("รหัสสินค้า")) in wms:
            skipped_wms += 1
            continue
        docs.append(make_doc(r, resolver.resolve(r.get("MR"), r.get("คลังสินค้า"))))

    existing = {d["row_key"] for d in col.find({"yearMonth": {"$in": yms}, "source": SOURCE}, {"_id": 0, "row_key": 1}).max_time_ms(30000)}
    keys = {d["row_key"] for d in docs}
    updated = sum(1 for d in docs if d["row_key"] in existing)
    inserted = len(docs) - updated
    missing = len(existing - keys - {r["row_key"] for r in rows if r.get("row_key")})

    if not dry_run and docs:
        stamp = datetime.now(timezone.utc)
        for i in range(0, len(docs), 500):
            ops = [UpdateOne({"row_key": d["row_key"]},
                             {"$set": {**d, "loadedAt": stamp},
                              "$setOnInsert": {"coreStatus": "pending", "coreQtyReturned": 0, "createdAt": stamp}},
                             upsert=True) for d in docs[i:i + 500]]
            col.bulk_write(ops, ordered=False)
            sleep(0.5)

    return {"months": yms, "fetched": fetched, "after_filters": len(rows), "inserted": inserted, "updated": updated,
            "skipped_wms": skipped_wms, "missing_in_atms": missing, "seconds": round(time.time() - t0, 1),
            "dry_run": dry_run, "filter_steps": steps}


def main() -> int:
    from pymongo import MongoClient
    from common import MONGODB_URI, JobLog, log

    job = JobLog("core_return_sync", "core-return-sync")
    dry = os.getenv("CORE_RETURN_SYNC_DRY_RUN") == "1" or os.getenv("DRY_RUN") == "1"
    try:
        client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=15000)
        res = run(client, int(os.getenv("CORE_RETURN_SYNC_MONTHS") or 3), dry)
    except Exception as e:  # noqa: BLE001
        log.error("core-return-sync failed: %s", e)
        job.finish("failed", error=str(e)[:500])
        return 1
    log.info("core-return-sync: %s", res)
    job.finish("success", **{k: v for k, v in res.items() if k != "filter_steps"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
