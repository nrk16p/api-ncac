"""เติม "ยอดรวมสุทธิ" ของใบ DD จากหน้า ATMS ลง atms.deposit_header (รันในเครื่อง)

  ดูก่อน (ไม่เขียน):  python3 scripts/atms_procurement/backfill_deposit_totals.py --limit 20
  เขียนจริง:          python3 scripts/atms_procurement/backfill_deposit_totals.py --write
  ทีละก้อน:           ... --write --limit 2000            (รันซ้ำได้ ข้ามใบที่มีแล้วเอง)
  เฉพาะใบที่ระบุ:      ... --write --dd LBDD26100363 LBDD26090768
  ดึงซ้ำทั้งที่มีแล้ว:   ... --write --refresh --from 2026-10-01

ทำไมต้องมี: `deposit_header.amount` มาจากหน้า /inv/deposit/index ซึ่งเป็นยอด "รวม" ก่อนภาษี
ส่วนยอดที่ต้องจ่ายจริง ("รวมสุทธิ") อยู่ในหน้า /inv/deposit/view/id/<id> เท่านั้น และ
**อัตราภาษีเป็นของแต่ละใบ DD** ไม่ใช่ของ PO และไม่ใช่ของผู้ขาย (ผู้ขายรายเดียวกันออกได้ทั้ง
7% และ 0% — ตรวจจริง 13/10/2026) คำนวณเองจาก PO จึงผิดได้ ต้องอ่านจากหน้า DD
pipeline_atms_procurement.py เก็บฟิลด์นี้ให้ใบใหม่อยู่แล้ว สคริปต์นี้ไว้เติมใบเก่าทีเดียวจบ

เขียนเฉพาะฟิลด์ grand_total / vat_rate / vat_amount / amount_net / totals_at
ไม่แตะ amount หรือฟิลด์อื่นที่ scraper หลักเป็นเจ้าของ · ATMS อืดง่าย ค่าตั้งต้นจึงยิงแค่
6 เธรดและพัก 0.15 วิ/ใบ ปรับด้วย --workers / --sleep
"""
import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import urllib3
from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
load_dotenv(os.path.join(HERE, "..", ".env"))
urllib3.disable_warnings()

from pipeline_atms_procurement import BASE, get_session, parse_deposit_totals  # noqa: E402


def parse_amount(v):
    try:
        return float(str(v or "0").replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="เขียนลงฐานจริง (ไม่ใส่ = ดูอย่างเดียว)")
    ap.add_argument("--limit", type=int, default=0, help="ทำไม่เกินกี่ใบในรอบนี้ (0 = ไม่จำกัด)")
    ap.add_argument("--from", dest="from_date", default="", help="เฉพาะใบที่ received_at ตั้งแต่ DD/MM/YYYY")
    ap.add_argument("--dd", nargs="*", default=[], help="เฉพาะเลขใบ DD ที่ระบุ")
    ap.add_argument("--refresh", action="store_true", help="ดึงซ้ำแม้ใบนั้นมี amount_net แล้ว")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--sleep", type=float, default=0.15, help="พักกี่วินาทีต่อใบในแต่ละเธรด")
    a = ap.parse_args()

    uri = os.getenv("MONGO_URI") or os.getenv("MONGODB_URI")
    if not uri:
        raise SystemExit("ไม่พบ MONGO_URI ใน scripts/.env")
    db = MongoClient(uri)["atms"]
    col = db["deposit_header"]

    q = {}
    if a.dd:
        q["deposit_code"] = {"$in": a.dd}
    if not a.refresh:
        q["amount_net"] = {"$exists": False}
        q["totals_missing"] = {"$ne": True}        # ใบเปล่าที่เปิดดูแล้วไม่มียอด — ไม่ต้องดึงซ้ำ
    docs = list(col.find(q, {"_id": 0, "deposit_id": 1, "deposit_code": 1,
                             "amount": 1, "received_at": 1, "purchase_order": 1}))
    # received_at เก็บเป็น "DD/MM/YYYY HH:MM" (สตริง) — กรองในเครื่องด้วยคีย์ปี/เดือน/วัน
    if a.from_date:
        def key(s):
            p = str(s or "").split(" ")[0].split("/")
            return (p[2], p[1], p[0]) if len(p) == 3 else ("", "", "")
        lo = key(a.from_date)
        docs = [d for d in docs if key(d.get("received_at")) >= lo]
    docs.sort(key=lambda d: str(d.get("deposit_code") or ""), reverse=True)   # ใบใหม่ก่อน
    if a.limit:
        docs = docs[:a.limit]
    print(f"ใบที่ต้องเติม {len(docs):,} ใบ · {'เขียนจริง' if a.write else 'ดูอย่างเดียว (dry-run)'}"
          f" · {a.workers} เธรด พัก {a.sleep}s/ใบ")
    if not docs:
        return

    s = get_session()
    ops, done, errors, changed, same, blanks = [], 0, 0, 0, 0, 0
    t0 = time.time()

    def work(d):
        for _ in range(3):
            try:
                r = s.get(f"{BASE}/inv/deposit/view/id/{d['deposit_id']}", timeout=45)
                r.raise_for_status()
                time.sleep(a.sleep)
                return d, parse_deposit_totals(r.text)
            except Exception:
                time.sleep(0.6)
        return d, None

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for fut in as_completed([ex.submit(work, d) for d in docs]):
            d, totals = fut.result()
            done += 1
            if not totals:
                # ใบเปล่ายอด 0.00 ไม่มีตารางรายการในหน้า DD เลย (386 ใบจาก 388 ที่อ่านไม่ได้
                # ตอนเติมรอบแรก) — ลงเครื่องหมายว่า "เปิดดูแล้วไม่มียอด" ไม่ปั้นตัวเลขขึ้นมาเอง
                # เพื่อให้เลิกไล่ดึงซ้ำทุกรอบ · ใบที่ยอดไม่เป็น 0 ถือว่ายิงพลาด ปล่อยให้ลองใหม่
                if parse_amount(d.get("amount")) == 0:
                    blanks += 1
                    ops.append(UpdateOne({"deposit_id": d["deposit_id"]},
                                         {"$set": {"totals_missing": True,
                                                   "totals_at": datetime.utcnow()}}))
                else:
                    errors += 1
                    print(f"   ✗ {d.get('deposit_code')} อ่านยอดไม่ได้ (ยอด {d.get('amount')})")
                continue
            raw = str(d.get("amount") or "").replace(",", "")
            net = totals["amount_net"].replace(",", "")
            if raw != net:
                changed += 1
                if changed <= 15 or not a.write:
                    print(f"   {d.get('deposit_code')}: amount {d.get('amount')} → "
                          f"รวมสุทธิ {totals['amount_net']} (ภาษี {totals.get('vat_rate', '?')}% "
                          f"{totals.get('vat_amount', '?')})")
            else:
                same += 1
            ops.append(UpdateOne({"deposit_id": d["deposit_id"]},
                                 {"$set": {**totals, "totals_at": datetime.utcnow()}}))
            if a.write and len(ops) >= 500:
                col.bulk_write(ops, ordered=False)
                ops = []
            if done % 200 == 0:
                print(f"   … {done:,}/{len(docs):,} ({time.time() - t0:.0f}s)")

    if a.write and ops:
        col.bulk_write(ops, ordered=False)
    print(f"\nอ่านได้ {done - errors - blanks:,} ใบ · ยอดต่างจาก amount {changed:,} ใบ · เท่ากัน {same:,} ใบ"
          f" · ใบเปล่ายอด 0 {blanks:,} ใบ · อ่านไม่ได้ {errors:,} ใบ · {time.time() - t0:.0f}s")
    print("เขียนลงฐานแล้ว" if a.write else "ยังไม่เขียน — ใส่ --write เมื่อพร้อม")


if __name__ == "__main__":
    main()
