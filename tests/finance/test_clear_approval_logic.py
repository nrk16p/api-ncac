"""อนุมัติการเคลียร์ — ตรรกะล้วน (ผู้ใช้สั่ง 19/10/2026)"""
import os
from decimal import Decimal

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

from services.finance import clear_approval_logic as cl  # noqa: E402
from services.finance.advance_logic import InvalidTransition, NotAllowed  # noqa: E402

TIERS = [
    {"clause": "6.7", "amount_max": Decimal("2000"), "min_level": 2, "approver_label": "Asst. Sup", "sort_order": 1},
    {"clause": "6.6", "amount_max": Decimal("5000"), "min_level": 3, "approver_label": "Sup", "sort_order": 2},
    {"clause": "6.5", "amount_max": Decimal("20000"), "min_level": 5, "approver_label": "MGR", "sort_order": 3},
    {"clause": "6.4", "amount_max": Decimal("100000"), "min_level": 8, "approver_label": "CL", "sort_order": 4},
]
AE = "580285"    # คุณเอ๋ ระดับ 2 ฝ่ายบัญชี
PU = "600181"    # คุณปู ระดับ 3
BANK = "690604"  # คุณแบงค์ ระดับ 5
PONG = "660057"  # คุณป้อง ระดับ 8 ฝ่ายบริหาร (ผูกฝ่ายบัญชีไว้)
PEOPLE = {
    AE:   {"employee_id": AE, "id": 1, "level": 2, "department_id": 6, "active": True},
    PU:   {"employee_id": PU, "id": 2, "level": 3, "department_id": 6, "active": True},
    BANK: {"employee_id": BANK, "id": 3, "level": 5, "department_id": 6, "active": True},
    PONG: {"employee_id": PONG, "id": 4, "level": 8, "department_id": 1, "active": True},
}
MAPS = {PONG: {6}}


def plan(amount, who=AE):
    return cl.plan(TIERS, PEOPLE, MAPS, who, amount)


def test_ตัดชั้นด้วยยอดที่ใช้จริงไม่ใช่ยอดที่เบิก():
    """เบิก 25,000 ใช้จริง 18,000 → ต้องเข้าข้อ 6.5 ไม่ใช่ 6.4"""
    p = plan(Decimal("18000"))
    assert p["clause"] == "6.5"
    assert [s["required_level"] for s in p["steps"]] == [3, 5]


def test_ใช้จริงน้อยลงชั้นอนุมัติลดตาม():
    assert plan(Decimal("1800"))["clause"] == "6.7"
    assert [s["step"] for s in plan(Decimal("1800"))["steps"]] == [1]


def test_ใช้เกินที่เบิกชั้นอนุมัติสูงขึ้น():
    p = plan(Decimal("30000"))
    assert p["clause"] == "6.4"
    assert [s["required_level"] for s in p["steps"]] == [3, 8]


def test_หัวหน้าถัดไปอนุมัติขั้น1ได้_ผู้เบิกอนุมัติเองไม่ได้():
    p = plan(Decimal("18000"))
    assert cl.can_approve(p, PEOPLE, MAPS, AE, PU, step=1)
    assert not cl.can_approve(p, PEOPLE, MAPS, AE, AE, step=1)


def test_ระดับไม่ถึงอนุมัติขั้น2ไม่ได้():
    p = plan(Decimal("30000"))          # ขั้น 2 ต้องระดับ 8
    assert not cl.can_approve(p, PEOPLE, MAPS, AE, BANK, step=2)
    assert cl.can_approve(p, PEOPLE, MAPS, AE, PONG, step=2)


def test_คนที่กดขั้น1แล้วกดขั้น2ไม่ได้():
    p = plan(Decimal("18000"))          # ขั้น 2 ต้องระดับ 5 คุณแบงค์ถึง
    assert cl.can_approve(p, PEOPLE, MAPS, AE, BANK, step=2)
    assert not cl.can_approve(p, PEOPLE, MAPS, AE, BANK, step=2, already=(PEOPLE[BANK]["id"],))


def test_ขั้นถัดไป():
    p = plan(Decimal("18000"))
    assert cl.next_step(p, 1) == 2
    assert cl.next_step(p, 2) is None
    assert cl.next_step(plan(Decimal("1800")), 1) is None


def test_ส่งเคลียร์ได้เฉพาะผู้เบิก():
    cl.check_submit("AWAITING_CLEARING", is_owner=True)
    with pytest.raises(NotAllowed):
        cl.check_submit("AWAITING_CLEARING", is_owner=False)


def test_อนุมัติได้เฉพาะตอนรออนุมัติเคลียร์():
    with pytest.raises(InvalidTransition):
        cl.check_approve(status_pending=False, eligible=True)
    with pytest.raises(NotAllowed):
        cl.check_approve(status_pending=True, eligible=False)
    cl.check_approve(status_pending=True, eligible=True)


def test_ไม่อนุมัติต้องมีเหตุผล():
    assert cl.check_reject(True, True, "  ใบเสร็จไม่ครบ ") == "ใบเสร็จไม่ครบ"
    with pytest.raises(Exception):
        cl.check_reject(True, True, "   ")


def test_แก้แล้วส่งใหม่_ขึ้นอนุมัติใหม่เฉพาะตอนข้ามชั้น():
    """ผู้ใช้เลือก 19/10/2026: แก้เล็กน้อยไม่กวนหัวหน้า แก้จนข้ามชั้นถึงขึ้นใหม่"""
    assert cl.needs_reapproval("6.5", "6.5") is False
    assert cl.needs_reapproval("6.5", "6.4") is True
    assert cl.needs_reapproval(None, "6.5") is True   # ยังไม่เคยอนุมัติ


def test_clause_for_ตรงกับ_plan():
    for amount in ("1800", "18000", "30000"):
        assert cl.clause_for(TIERS, Decimal(amount)) == plan(Decimal(amount))["clause"]
