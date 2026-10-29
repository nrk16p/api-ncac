"""ข้อความที่ผู้ใช้เห็นต้องเป็นภาษาไทย (พบจาก UAT 19/10/2026)

สองจุดนี้กันผิดถูกอยู่แล้ว แต่พูดภาษาอังกฤษใส่ผู้ใช้
- เคลียร์โดยไม่มีรายการ: Pydantic min_length ยิงก่อน ข้อความไทยใน advance_logic เลยไม่มีวันได้ออก
- อนุมัติเกินอำนาจ: ข้อความใน form_approval_routes เป็นอังกฤษ
"""
import os
import re

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

from schemas.finance_schema import ClearIn  # noqa: E402
from services.finance import advance_logic as logic  # noqa: E402

THAI = re.compile(r"[฀-๿]")


def test_เคลียร์ไม่มีรายการ_ผ่าน_schema_ไปถึงกติกาไทย():
    """schema ต้องไม่ดักก่อน ไม่งั้นผู้ใช้เห็นข้อความอังกฤษของ Pydantic"""
    body = ClearIn(action_by="610197", clear_date="2026-10-19", items=[])
    assert body.items == []


def test_เคลียร์ไม่มีรายการ_ได้ข้อความไทย():
    with pytest.raises(logic.AdvanceRuleError) as exc:
        logic.check_clear_items([])
    assert str(exc.value) == "กรุณาระบุรายการค่าใช้จ่ายอย่างน้อย 1 รายการ"


def test_ข้อความในเส้นทางอนุมัติเป็นภาษาไทยทั้งหมด():
    """ไล่อ่านไฟล์จริง: ทุก HTTPException ที่ผู้ใช้เห็นต้องมีอักษรไทย"""
    src = open("routes/forms/form_approval_routes.py", encoding="utf-8").read()
    english = [
        m for m in re.findall(r'HTTPException\(\s*\d{3}\s*,\s*"([^"]+)"', src)
        if not THAI.search(m)
    ]
    assert english == [], f"ยังมีข้อความอังกฤษเหลือ: {english}"
