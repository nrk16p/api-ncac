"""
ตรวจความครบถ้วน ATMS ↔ Mongo อย่างเดียว (ไม่ดึงข้อมูล) — ~8 request ใช้เวลาไม่กี่วินาที
ใช้กับปุ่ม "ตรวจเดี๋ยวนี้" ในหน้า /ap-tracking/audit (mena-wms) เพื่อไม่ต้องรอรอบ 06:00
รอบเต็ม atms_procurement ก็เรียก audit_completeness ตัวเดียวกันนี้ต่อท้ายทุกวันอยู่แล้ว
"""

import os
import sys
from datetime import datetime, timedelta

from pymongo import MongoClient

from pipeline_atms_procurement import audit_completeness, get_session, log


def main():
    uri = os.getenv("MONGODB_URI")
    if not uri:
        log("MONGODB_URI not set"); sys.exit(1)
    ict = datetime.utcnow() + timedelta(hours=7)          # Asia/Bangkok
    client = MongoClient(uri, serverSelectionTimeoutMS=10_000)
    try:
        doc = audit_completeness(get_session(), client["atms"], f"{ict.year}-{ict.month:02d}")
    finally:
        client.close()
    if not doc["ok"]:
        log("พบความไม่ตรงกัน", doc["totals"], "· ตกหล่นรายการสินค้า", doc["missing_items_total"])


if __name__ == "__main__":
    main()
