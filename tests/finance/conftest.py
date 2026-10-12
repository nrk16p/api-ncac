"""fixture ร่วมของเทสต์เบิกเงินสดย่อย

อยู่ใน conftest เพราะ `tests/` ไม่ใช่ python package — `from tests.finance.x import y`
พังทั้งไฟล์ (เป็นเหตุเดียวกับที่ test_approval_repo.py / test_approval_steps.py collect ไม่ได้)
"""
from datetime import date

import pytest


WEIGH_ITEMS = (
    [{"event_date": date(2026, 9, 17), "truck_code": "161", "truck_plate": "71-4285",
      "truck_type": "โม่ใหญ่", "ref_no": "000055", "qty": 1, "rate": 100}] * 12
    + [{"event_date": date(2026, 9, 20), "truck_code": "153", "truck_plate": "71-4728",
        "truck_type": "โม่ใหญ่", "ref_no": "000101", "qty": 1, "rate": 120}] * 3
    + [{"event_date": date(2026, 9, 24), "truck_code": "165", "truck_plate": "71-9001",
        "truck_type": "โม่เล็ก", "ref_no": "000172", "qty": 1, "rate": 80}]
)


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
