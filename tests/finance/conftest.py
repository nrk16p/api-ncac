"""fixture ร่วมของเทสต์เบิกเงินสดย่อย

อยู่ใน conftest เพราะ `tests/` ไม่ใช่ python package — `from tests.finance.x import y`
พังทั้งไฟล์ (เป็นเหตุเดียวกับที่ test_approval_repo.py / test_approval_steps.py collect ไม่ได้)
"""
from datetime import date

import pytest


# ใบจริง เลขที่ 093787 ลงวันที่ 25/9/69 รวม 1,640.00 — 16 คัน 3 ราคา ระหว่าง 17–24/9
# ทุกแถวมีเลขที่บัตรชั่งของตัวเอง (ของจริงก็เป็นอย่างนั้น) และรถ 1093 ปรากฏสองแถวตามที่หมายเหตุเขียนไว้
def _row(day, code, plate, truck_type, ref, rate):
    return {"event_date": date(2026, 9, day), "truck_code": code, "truck_plate": plate,
            "truck_type": truck_type, "ref_no": ref, "qty": 1, "rate": rate}


WEIGH_ITEMS = [
    _row(17, "161", "71-4285", "โม่ใหญ่", "000055", 100),
    _row(17, "144", "71-4286", "โม่ใหญ่", "000056", 100),
    _row(18, "F014", "71-4301", "โม่ใหญ่", "000061", 100),
    _row(18, "F016", "71-4302", "โม่ใหญ่", "000062", 100),
    _row(19, "113", "71-4310", "โม่ใหญ่", "000070", 100),
    _row(19, "118", "71-4311", "โม่ใหญ่", "000071", 100),
    _row(20, "145", "71-4320", "โม่ใหญ่", "000080", 100),
    _row(20, "340", "71-4321", "โม่ใหญ่", "000081", 100),
    _row(21, "1126", "71-4330", "โม่เล็ก", "000090", 100),
    _row(21, "1145", "71-4331", "โม่เล็ก", "000091", 100),
    _row(22, "1093", "71-4332", "โม่เล็ก", "000100", 100),
    _row(22, "1093", "71-4332", "โม่เล็ก", "000101", 100),  # ชั่ง 2 เที่ยว คนละบัตร
    _row(23, "153", "71-4728", "โม่ใหญ่", "000110", 120),
    _row(23, "146", "71-4729", "โม่ใหญ่", "000111", 120),
    _row(24, "151", "71-4730", "โม่ใหญ่", "000112", 120),
    _row(24, "165", "71-9001", "โม่เล็ก", "000172", 80),
]


@pytest.fixture
def weigh_items():
    """16 แถว 3 ราคา = 1,640.00 ตามใบจริง เลขที่ 093787"""
    return [dict(i) for i in WEIGH_ITEMS]


@pytest.fixture
def fund(db_session):
    from models.finance_petty_model import FinPettyExpenseType, FinPettyFund
    row = FinPettyFund(branch_code="03", division_code="B02", holder_employee_id="12345")
    db_session.add(FinPettyExpenseType(code="WEIGH", name_th="ค่าชั่งน้ำหนัก", spec={"item_based": True}))
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def claim_payload(fund):
    def _make(**over):
        base = {"fund_id": fund.id, "expense_type": "WEIGH", "expense_date": date(2026, 9, 25),
                "payee_name": "อัญชัญ น่วมมี", "created_by": "55555"}
        base.update(over)
        return base
    return _make
