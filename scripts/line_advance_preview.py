# -*- coding: utf-8 -*-
"""ดูหน้าตาข้อความไลน์ของงาน Advance ทั้ง 3 จังหวะ และส่งทดสอบเข้ากลุ่มจริง

ดูอย่างเดียว:  python scripts/line_advance_preview.py
ส่งจริง:      FINANCE_LINE_TOKEN=xxxxx python scripts/line_advance_preview.py --send
ส่งเข้ากลุ่มอื่น: เพิ่ม FINANCE_LINE_GROUP_ID=Cxxxx
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FE_BASE_URL", "https://menait-service.vercel.app")

from services.finance import finance_line as fl  # noqa: E402

SAMPLES = [
    (fl.SUBMITTED, {
        "form_id": "ADV-2610-900", "requester_name": "[ทดสอบ] ดารณี ฤทธิจีน", "department": "แผนกบัญชี",
        "amount": 25000, "purpose": "[ทดสอบระบบ] ค่าเดินทางต่างจังหวัด", "use_date": "2026-10-22",
    }),
    (fl.READY_FOR_VOUCHER, {
        "form_id": "ADV-2610-900", "requester_name": "[ทดสอบ] ดารณี ฤทธิจีน", "department": "แผนกบัญชี",
        "amount": 25000, "purpose": "[ทดสอบระบบ] ค่าเดินทางต่างจังหวัด", "use_date": "2026-10-22",
        "approver_name": "เจษฎา รองศักดิ์",
    }),
    (fl.CLEARING_APPROVAL, {
        "form_id": "ADV-2610-900", "requester_name": "[ทดสอบ] ดารณี ฤทธิจีน", "department": "แผนกบัญชี",
        "amount_paid": 25000, "amount_actual": 9500, "settle_amount": 15500,
        "purpose": "[ทดสอบระบบ] ค่าเดินทางต่างจังหวัด", "step": 1, "total_steps": 2, "clause": "6.5",
    }),
]

send = "--send" in sys.argv
for event, ctx in SAMPLES:
    text = fl.render_line_message(event, {**ctx, "fe_base_url": os.getenv("FE_BASE_URL")})
    print(f"\n----- {event} -----")
    print(text)
    if send:
        ok = fl.send_line(text)
        print(f"[ส่งเข้ากลุ่ม {fl.group_id()}: {'สำเร็จ' if ok else 'ไม่สำเร็จ'}]")

if not send:
    print("\n(ดูอย่างเดียว ยังไม่ได้ส่ง — เพิ่ม --send พร้อมตั้ง LINE_TOKEN เพื่อส่งจริง)")
