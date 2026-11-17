"""
Pipeline: atms_pr_quick — รีเฟรชรายการ PR ทุกชั่วโมง (07:15–20:15 น. ไทย) ให้หน้า /safety-stock ของ mena-wms

ทำไมแยกจาก atms_procurement_light: รอบ light ยิง ATMS ~3,500 ครั้ง/รอบ (เปิดหน้ารายละเอียด PR+PO ทุกใบ
ในช่วง 30 วันซ้ำทุกรอบ) รันทุกชั่วโมงแล้ว ATMS รับไม่ไหว · รอบนี้ทำแค่ส่วนที่หน้าจุดสั่งซื้อต้องการให้สดเร็ว:
  1. รายการ PR (หัวใบ + สถานะอนุมัติ) ย้อนหลังช่วงเดียวกับรอบเต็ม — ตั้ง 1,000 แถว/หน้า เหลือ ~5 requests
  2. ลบใบที่ถูกลบใน ATMS (prune_missing_prs — กันพลาดชุดเดียวกับรอบเต็ม)
  3. รายการสินค้าเฉพาะ PR ที่ยังไม่มีรายการใน Mongo (= ใบเปิดใหม่) ย้อนหลัง ATMS_PR_QUICK_ITEM_DAYS วัน
  4. หัวใบ PO ย้อน ATMS_PO_DEEP_DAYS วัน (หน้า list) — สถานะการรับของสดทุกชั่วโมง ใบที่รับครบหายจาก
     "กำลังสั่งซื้อ" ในชั่วโมงนั้น (สโตร์ขอ 17/11/2026) · รายการสินค้า PO ดึงซ้ำเฉพาะใบที่สถานะเปลี่ยน + ใบใหม่
DD / รายการสินค้าที่เหลือ ยังมาจากรอบ full + light ทุก 4 ชม. เหมือนเดิม · ลบ PO ที่หายยังทำเฉพาะรอบเต็ม
หลังจบ routes/pipeline ยิง webhook ให้ mena-wms คำนวณ /safety-stock ใหม่ (source=pr-hourly)
Run log: atms.procurement_runs (pipeline="atms_pr_quick")
"""

import os
import sys
from datetime import datetime, timedelta

from pymongo import MongoClient

from pipeline_atms_procurement import (
    BASE, MONGO_URI, PO_KEY, _get, _scrape_items, get_session, log, po_deep_from, prune_missing_prs, scrape_po, scrape_pr,
)

PO_STATUS_KEY = "สถานะการรับสินค้า"
OPEN_PO_RE = "บางส่วน|ยังไม่ได้รับ"


def refresh_po_status(s, db, ict):
    """ดึงหัวใบ PO (สถานะการรับของ) ย้อนลึกเท่ารอบเต็ม แล้วดึงรายการสินค้าซ้ำเฉพาะใบที่ต้องดึง:
    ใบที่ค้างรับก่อนรอบนี้แล้วสถานะเปลี่ยน (รับบางส่วน → ยอดค้างรับรายบรรทัดเปลี่ยน) + ใบที่ยังไม่มีรายการเลย (ใบใหม่)
    คืน counts ที่ต้องเขียนลง procurement_runs — po_from บอกหน้าต่าง PO จริงให้ mena-wms ใช้ตรวจใบที่ถูกลบ"""
    col = db["purchase_orders"]
    before = {
        d[PO_KEY]: d.get(PO_STATUS_KEY)
        for d in col.find({PO_STATUS_KEY: {"$regex": OPEN_PO_RE}}, {"_id": 0, PO_KEY: 1, PO_STATUS_KEY: 1}, max_time_ms=30_000)
        if d.get(PO_KEY)
    }
    po_from = po_deep_from(ict)
    seen = set()
    heads = scrape_po(s, po_from, db, seen)
    after = {
        d[PO_KEY]: d.get(PO_STATUS_KEY)
        for d in col.find({PO_KEY: {"$in": list(before)}}, {"_id": 0, PO_KEY: 1, PO_STATUS_KEY: 1}, max_time_ms=30_000)
    } if before else {}
    changed = {c for c, st in before.items() if c in after and after[c] != st}

    def pick(pairs):
        pairs = [p for p in pairs if p[0] in seen]
        codes = [p[0] for p in pairs]
        have = set(db["purchase_order_items"].distinct("po_code", {"po_code": {"$in": codes}})) if codes else set()
        return [p for p in pairs if p[0] in changed or p[0] not in have]

    items = _scrape_items(s, "po", po_from, db, workers=2, pick=pick)
    return {"purchase_orders": heads, "po_from": po_from, "po_status_changed": len(changed), "po_items": items}

LABEL = "atms_pr_quick"


def main():
    if not MONGO_URI:
        log("MONGODB_URI not set"); sys.exit(1)
    started = datetime.utcnow()
    ict = started + timedelta(hours=7)
    # หน้าต่างเดียวกับรอบเต็ม (วันที่ 1 ของ 2 เดือนก่อน) — prune จึงครอบคลุมใบเก่าที่ถูกลบด้วย (เคส KKPR26070020 ก.ค.)
    y, mth = ict.year, ict.month - 2
    while mth <= 0:
        mth += 12
        y -= 1
    from_date = f"01/{mth:02d}/{y}"
    item_from = (ict - timedelta(days=int(os.getenv("ATMS_PR_QUICK_ITEM_DAYS", "3")))).strftime("%d/%m/%Y")

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10_000)
    db = client["atms"]
    counts, err = {}, None
    try:
        s = get_session()
        # ค่าราย session — ลด ~40 หน้าเหลือ ~5 หน้า (ดู memory ref_atms_procurement_data)
        _get(s, f"{BASE}/account/user/set.row.per.page/", params={"row-per-page": "1000"})
        seen = set()
        counts["purchase_requests"] = scrape_pr(s, from_date, db, seen); log("PR", counts["purchase_requests"])
        if os.getenv("ATMS_PR_PRUNE", "1") != "0":
            counts["pr_pruned"] = prune_missing_prs(db, from_date, seen, started)

        def only_new(pairs):
            # pr_code มี index (ensure_index ใน _scrape_items) — $in ไม่กี่ร้อยใบ เบา
            have = set(db["purchase_request_items"].distinct("pr_code", {"pr_code": {"$in": [p[0] for p in pairs]}}))
            return [p for p in pairs if p[0] not in have]

        counts["items"] = _scrape_items(s, "pr", item_from, db, workers=2, pick=only_new)
        log("new PR items", counts["items"])
        if os.getenv("ATMS_PR_QUICK_PO", "1") != "0":
            counts.update(refresh_po_status(s, db, ict))
            log("PO", counts["po_from"], counts["purchase_orders"], "สถานะเปลี่ยน", counts["po_status_changed"], "items", counts["po_items"])
    except Exception as e:
        err = str(e)
        log("ERROR:", err)

    finished = datetime.utcnow()
    db["procurement_runs"].insert_one({
        "pipeline": LABEL, "created_at": finished,
        "started_at": started, "finished_at": finished, "from_date": from_date,
        "counts": counts, "ok": err is None, "error": err,
        "duration_sec": round((finished - started).total_seconds(), 1),
    })
    client.close()
    if err:
        sys.exit(1)
    log("done", counts)


if __name__ == "__main__":
    main()
