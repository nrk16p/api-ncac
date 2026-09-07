"""
Light variant ของ atms_stockmovement — ดึงเฉพาะ "เดือนปัจจุบัน" 1 request (~1-2 นาที)
ใช้กับรอบทุก 4 ชั่วโมงระหว่างวัน ส่วนรอบเช้าเป็นตัวเต็มที่ย้อน 5 เดือน (SM_MONTHS=5)
เพื่อเก็บรายการที่ ATMS คีย์ย้อนหลัง (รับได้ ~2 เดือน) และรายการที่ถูกลบ

run-log แยก label "atms_stockmovement_light" ใน atms.stockmovement_runs

⚠️ ต้อง setdefault ก่อน import — ค่าคงที่ในตัวหลักอ่าน env ตอน import ไม่ใช่ตอนเรียก main()
"""

import os

os.environ.setdefault("SM_MONTHS", "1")
os.environ.setdefault("SM_RUN_LABEL", "atms_stockmovement_light")

from pipeline_atms_stockmovement import main  # noqa: E402

if __name__ == "__main__":
    main()
