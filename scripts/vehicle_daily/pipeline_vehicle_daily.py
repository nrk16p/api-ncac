"""vehicle_daily — ATMS รายงานรถประจำวัน (vehicle.daily.transaction) ทุกกลุ่มฟลีท → atms.vehicle_daily_transaction

หนึ่งแถว = รถหนึ่งคันในหนึ่งวัน (หัว/หาง/คนขับ/สเตตัส/หัวหน้า/โอนข้ามแพลนท์/ลางาน/ซ่อม/หมายเหตุ)
ดึงทีละ (กลุ่มฟลีท × เดือน) ครั้งเดียวทั้งเดือน (num_of_day = จำนวนวัน) เดือนปัจจุบันถึงวันนี้

Env (จาก POST /pipeline/run/vehicle_daily body):
  MONTHS  "MM/YYYY,MM/YYYY"  ค่าเริ่มต้น = เดือนที่แล้ว + เดือนนี้ (เวลาไทย)
  FLEET_GROUPS "1,2"         ค่าเริ่มต้น = ทุกกลุ่มที่อ่านได้จากหน้าฟอร์ม ATMS
  VEHICLE_DAILY_COLLECTION   ใช้ตอนทดสอบเท่านั้น ต้องขึ้นต้นด้วย vehicle_daily_transaction

เขียนแบบแทนที่ทั้งก้อน (กลุ่ม × เดือน): upsert ตาม row_key แล้วลบแถวของรอบเก่าที่ไม่มีในรอบนี้
(รถย้ายกลุ่ม/ถูกลบใน ATMS จะไม่ค้าง) · ATMS ตอบเป็นหน้า HTML (session หลุด/สร้างรายงานไม่ได้)
→ login ใหม่แล้วลองอีกครั้ง ยังไม่ได้ก็ข้ามกลุ่มนั้น ไม่ลบอะไร และจบรอบเป็น failed
"""
import calendar
import io
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from pymongo import ASCENDING, MongoClient, ReplaceOne

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engineon"))
from common import BASE_URL, MONGODB_URI, JobLog, atms_session, log, now_bkk  # noqa: E402

REPORT = "vehicle.daily.transaction"
FORM_URL = f"{BASE_URL}/report/excel/index.excel/type/{REPORT}"
PRINT_URL = f"{BASE_URL}/report/print.out/print.excel/type/{REPORT}"
DEFAULT_COLLECTION = "vehicle_daily_transaction"
TARGET_RE = re.compile(r"^vehicle_daily_transaction(_[a-z0-9_]+)?$")
PACE_S = 3
BATCH = 1000
# กลุ่มที่เคยมีข้อมูลเยอะแล้วรอบนี้ได้ 0 แถว = น่าจะ ATMS สะดุด ไม่ใช่รถหายหมดจริง → ไม่ลบของเดิม
EMPTY_GUARD = 50

# คอลัมน์ตามตำแหน่ง (หัวตาราง 2 ชั้นในไฟล์ ATMS) — ชื่อห้ามมีจุด เพราะ Mongo อ่านเป็น path ซ้อน
COLUMNS = [
    "วันที่", "ฟลีท", "ลำดับ", "ผู้ว่าจ้าง", "แพลนท์_รหัส", "แพลนท์_ชื่อ",
    "ยี่ห้อ", "เบอร์รถ", "ทะเบียน", "คิว", "หาง_เบอร์รถ", "หาง_ทะเบียน",
    "คนขับ_สถานะ", "คนขับ_รหัส", "คนขับ_ชื่อ", "คนขับ_เบอร์โทร",
    "สเตตัส_รถ", "สเตตัส_คนขับ", "หัวหน้า", "รถจีน",
    "โอนรถข้ามแพลนท์_1", "โอนรถข้ามแพลนท์_2", "Logoff", "Logon",
    "ลางาน", "ซ่อม", "หมายเหตุ", "วัคซีน",
]
# เช็กหัวตารางก่อนเชื่อตำแหน่ง: ATMS เพิ่ม/ย้ายคอลัมน์เมื่อไหร่ต้องล้ม ไม่ใช่เขียนข้อมูลผิดช่องเงียบ ๆ
HEADER_CHECKS = {(1, 0): "วันที่", (1, 1): "ฟลีท", (1, 6): "หัว", (2, 8): "ทะเบียน", (1, 10): "หาง",
                 (1, 12): "คนขับรถ", (1, 16): "สเตตัส", (1, 27): "วัคซีน"}


def is_excel(content: bytes) -> bool:
    return content[:4] == b"PK\x03\x04" or content[:2] == b"\xd0\xcf"


def norm_plate(plate: str | None) -> str | None:
    """'สบ.71-4372' → '71-4372' (ใช้ join กับระบบที่เก็บทะเบียนไม่มีจังหวัด)"""
    if not plate:
        return None
    return re.sub(r"\s+", "", plate.replace("สบ.", "")) or None


def default_months(today: datetime) -> list[tuple[int, int]]:
    prev = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return [prev, (today.year, today.month)]


def parse_months(raw: str | None, today: datetime) -> list[tuple[int, int]]:
    if not raw:
        return default_months(today)
    out = []
    for part in raw.split(","):
        m = re.fullmatch(r"\s*(\d{1,2})/(\d{4})\s*", part)
        if not m or not 1 <= int(m.group(1)) <= 12:
            raise ValueError(f"MONTHS ต้องเป็น MM/YYYY คั่นด้วย , — ได้ {part!r}")
        out.append((int(m.group(2)), int(m.group(1))))
    return out


def month_window(year: int, month: int, today: datetime) -> tuple[str, int] | None:
    """(t_date วันที่ 1, num_of_day) — เดือนปัจจุบันตัดที่วันนี้ · เดือนในอนาคต = None"""
    if (year, month) > (today.year, today.month):
        return None
    days = calendar.monthrange(year, month)[1]
    if (year, month) == (today.year, today.month):
        days = today.day
    return f"01/{month:02d}/{year}", days


def fleet_groups_from_form(html: str) -> dict[str, str]:
    m = re.search(r'<select[^>]*name="fleet_group_id"[^>]*>(.*?)</select>', html, re.S)
    if not m:
        return {}
    return {v: n.strip() for v, n in re.findall(r'<option[^>]*value="(\d+)"[^>]*>([^<]*)', m.group(1))}


def parse_report(raw: pd.DataFrame, fleet_group_id: str, fleet_group_name: str,
                 year: int, month: int, run_id: str, run_at: datetime) -> list[dict]:
    """ไฟล์ ATMS (อ่านแบบ header=None, dtype=str) → เอกสาร Mongo · แถว 0 = ช่วงวันที่, 1–2 = หัวตาราง"""
    if raw.shape[1] != len(COLUMNS):
        raise ValueError(f"ATMS เปลี่ยนรูปแบบรายงาน: {raw.shape[1]} คอลัมน์ (คาด {len(COLUMNS)})")
    for (r, c), want in HEADER_CHECKS.items():
        got = str(raw.iat[r, c]).strip() if r < len(raw) else ""
        if got != want:
            raise ValueError(f"ATMS เปลี่ยนหัวตาราง: แถว {r} คอลัมน์ {c} = {got!r} (คาด {want!r})")

    body = raw.iloc[3:].copy()
    body.columns = COLUMNS
    body = body[body["วันที่"].notna() & body["ทะเบียน"].notna()]
    body = body.astype(object).where(body.notna(), None)

    docs, seen = {}, 0
    ym = f"{year}-{month:02d}"
    for row in body.to_dict("records"):
        rec = {k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
        rec = {k: (None if v == "" else v) for k, v in rec.items()}
        d = datetime.strptime(rec["วันที่"], "%d/%m/%Y")
        if (d.year, d.month) != (year, month):
            continue  # กันไว้เผื่อ ATMS แถมวันนอกเดือนมา จะได้ไม่ไปทับข้อมูลของเดือนอื่น
        if rec["ลำดับ"] is not None:
            try:
                rec["ลำดับ"] = int(float(rec["ลำดับ"]))
            except ValueError:
                pass
        key = f"{fleet_group_id}|{rec['วันที่']}|{rec['ทะเบียน']}"
        seen += 1
        docs[key] = {
            "_id": key,
            **rec,
            "date": d,
            "year_month": ym,
            "plate_norm": norm_plate(rec["ทะเบียน"]),
            "trailer_plate_norm": norm_plate(rec["หาง_ทะเบียน"]),
            "fleet_group_id": int(fleet_group_id),
            "fleet_group_name": fleet_group_name,
            "etl_run_id": run_id,
            "etl_run_at": run_at,
        }
    if seen != len(docs):
        log.warning("กลุ่ม %s %s: แถวซ้ำ (วันที่+ทะเบียน) %d แถว — เก็บแถวท้ายสุด", fleet_group_id, ym, seen - len(docs))
    return list(docs.values())


def fetch_report(session, fleet_group_id: str, t_date: str, num_of_day: int) -> bytes | None:
    r = session.post(PRINT_URL, data={
        "fleet_group_id": fleet_group_id, "fleet_id": "", "t_date": t_date,
        "num_of_day": str(num_of_day), "submit": "พิมพ์",
        "display_type": "multiple-day", "report_type": REPORT,
    }, headers={"Referer": f"{FORM_URL}?t_date={t_date}"}, timeout=300)
    return r.content if r.status_code == 200 and is_excel(r.content) else None


def ensure_indexes(col) -> None:
    col.create_index([("date", ASCENDING)])
    col.create_index([("plate_norm", ASCENDING), ("date", ASCENDING)])
    col.create_index([("fleet_group_id", ASCENDING), ("year_month", ASCENDING)])
    col.create_index([("คนขับ_รหัส", ASCENDING), ("date", ASCENDING)])


def write_chunk(col, docs: list[dict], fleet_group_id: int, ym: str, run_id: str) -> dict:
    """upsert ตาม _id แล้วลบแถวของ (กลุ่ม × เดือน) ที่รอบนี้ไม่ได้แตะ"""
    upserted = modified = 0
    for i in range(0, len(docs), BATCH):
        res = col.bulk_write([ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in docs[i:i + BATCH]],
                             ordered=False)
        upserted += res.upserted_count
        modified += res.modified_count
    deleted = col.delete_many({"fleet_group_id": fleet_group_id, "year_month": ym,
                               "etl_run_id": {"$ne": run_id}}).deleted_count
    return {"rows": len(docs), "inserted": upserted, "modified": modified, "deleted": deleted}


def run(env: dict) -> dict:
    today = now_bkk().replace(tzinfo=None)
    months = parse_months(env.get("MONTHS"), today)
    target = env.get("VEHICLE_DAILY_COLLECTION") or DEFAULT_COLLECTION
    if not TARGET_RE.match(target):
        raise ValueError(f"VEHICLE_DAILY_COLLECTION ต้องขึ้นต้นด้วย {DEFAULT_COLLECTION}: {target!r}")

    session = atms_session()
    groups = fleet_groups_from_form(session.get(FORM_URL, timeout=60).text)
    if not groups:
        raise RuntimeError("อ่านรายชื่อกลุ่มฟลีทจากหน้าฟอร์ม ATMS ไม่ได้ (session หลุด?)")
    if env.get("FLEET_GROUPS"):
        wanted = [g.strip() for g in env["FLEET_GROUPS"].split(",") if g.strip()]
        unknown = [g for g in wanted if g not in groups]
        if unknown:
            raise ValueError(f"FLEET_GROUPS ไม่มีใน ATMS: {unknown} (มี {sorted(groups, key=int)})")
        groups = {g: groups[g] for g in wanted}

    col = MongoClient(MONGODB_URI)["atms"][target]
    ensure_indexes(col)
    run_id = uuid.uuid4().hex
    run_at = datetime.now(timezone.utc)
    results, failed = [], []

    for year, month in months:
        window = month_window(year, month, today)
        if not window:
            log.info("ข้าม %02d/%d (เดือนในอนาคต)", month, year)
            continue
        t_date, num_of_day = window
        ym = f"{year}-{month:02d}"
        for gid in sorted(groups, key=int):
            name = groups[gid]
            content = fetch_report(session, gid, t_date, num_of_day)
            if content is None:
                log.warning("กลุ่ม %s %s: ATMS ไม่ส่งไฟล์ — login ใหม่แล้วลองอีกครั้ง", gid, ym)
                time.sleep(PACE_S)
                session = atms_session()
                content = fetch_report(session, gid, t_date, num_of_day)
            time.sleep(PACE_S)
            if content is None:
                log.error("กลุ่ม %s (%s) %s: ดึงไม่ได้ ข้ามกลุ่มนี้ ไม่ลบของเดิม", gid, name, ym)
                failed.append({"fleet_group_id": int(gid), "year_month": ym, "error": "not_excel"})
                continue
            raw = pd.read_excel(io.BytesIO(content), header=None, dtype=str)
            docs = parse_report(raw, gid, name, year, month, run_id, run_at)
            if not docs:
                existing = col.count_documents({"fleet_group_id": int(gid), "year_month": ym})
                if existing > EMPTY_GUARD:
                    log.error("กลุ่ม %s (%s) %s: รอบนี้ 0 แถว แต่ของเดิม %d แถว — ไม่ลบ", gid, name, ym, existing)
                    failed.append({"fleet_group_id": int(gid), "year_month": ym, "error": "empty_guard",
                                   "existing": existing})
                    continue
            stat = write_chunk(col, docs, int(gid), ym, run_id)
            log.info("กลุ่ม %s (%s) %s: %s", gid, name, ym, stat)
            results.append({"fleet_group_id": int(gid), "fleet_group_name": name, "year_month": ym, **stat})

    return {"target": target, "months": [f"{m:02d}/{y}" for y, m in months], "run_id": run_id,
            "records": sum(r["rows"] for r in results), "deleted": sum(r["deleted"] for r in results),
            "groups": results, "failed": failed}


def main() -> None:
    env = dict(os.environ)
    meta = {k.lower(): env[k] for k in ("MONTHS", "FLEET_GROUPS", "VEHICLE_DAILY_COLLECTION") if env.get(k)}
    job = JobLog("vehicle_daily", "vehicle_daily", meta)
    try:
        summary = run(env)
    except Exception as e:
        job.finish("failed", error=str(e))
        raise
    if summary["failed"]:
        job.finish("failed", error=f"{len(summary['failed'])} กลุ่ม×เดือน ดึง/เขียนไม่ได้", **summary)
        log.error("จบแบบไม่ครบ: %s", summary["failed"])
        sys.exit(1)
    job.finish("success", **summary)
    log.info("vehicle_daily OK — %d แถว, ลบของเก่า %d", summary["records"], summary["deleted"])


if __name__ == "__main__":
    main()
