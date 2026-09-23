"""
Pipeline: atms_pr_quick — รีเฟรชรายการ PR ทุกชั่วโมง (07:15–20:15 น. ไทย) ให้หน้า /safety-stock ของ mena-wms

ทำไมแยกจาก atms_procurement_light: รอบ light ยิง ATMS ~3,500 ครั้ง/รอบ (เปิดหน้ารายละเอียด PR+PO ทุกใบ
ในช่วง 30 วันซ้ำทุกรอบ) รันทุกชั่วโมงแล้ว ATMS รับไม่ไหว · รอบนี้ทำแค่ส่วนที่หน้าจุดสั่งซื้อต้องการให้สดเร็ว:
  1. รายการ PR (หัวใบ + สถานะอนุมัติ) ย้อนหลังช่วงเดียวกับรอบเต็ม — ตั้ง 1,000 แถว/หน้า เหลือ ~5 requests
  2. ลบใบที่ถูกลบใน ATMS (prune_missing_prs — กันพลาดชุดเดียวกับรอบเต็ม)
  3. รายการสินค้าเฉพาะ PR ที่ยังไม่มีรายการใน Mongo (= ใบเปิดใหม่) ย้อนหลัง ATMS_PR_QUICK_ITEM_DAYS วัน
PO / DD / รายการสินค้าทั้งหมด ยังมาจากรอบ full + light ทุก 4 ชม. เหมือนเดิม
หลังจบ routes/pipeline ยิง webhook ให้ mena-wms คำนวณ /safety-stock ใหม่ (source=pr-hourly)
Run log: atms.procurement_runs (pipeline="atms_pr_quick")
"""

import os
import sys
from datetime import datetime, timedelta

from pymongo import MongoClient

from pipeline_atms_procurement import (
    BASE, MONGO_URI, _get, _scrape_items, get_session, log, prune_missing_prs, scrape_pr,
)

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
