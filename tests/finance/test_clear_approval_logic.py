"""อนุมัติการเคลียร์ — ตรรกะล้วน (ผู้ใช้สั่ง 19/10/2026) · ตั้งแต่ 26/10/2026 route ส่งยอดในใบขอเข้ามา
ขั้นจึงเหมือนตอนขอเบิกเสมอ ไม่ว่าใช้จริงเท่าไหร่ (ดู test_flow_e2e_sqlite)"""
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
    AE:   {"employee_id": AE, "id": 1, "level": 2, "department_id": 6, "division_id": 6, "active": True},
    PU:   {"employee_id": PU, "id": 2, "level": 3, "department_id": 6, "division_id": 6, "active": True},
    BANK: {"employee_id": BANK, "id": 3, "level": 5, "department_id": 6, "division_id": 6, "active": True},
    PONG: {"employee_id": PONG, "id": 4, "level": 8, "department_id": 1, "division_id": 1, "active": True},
}
MAPS = {PONG: {6}}


def plan(amount, who=AE):
    return cl.plan(TIERS, PEOPLE, MAPS, who, amount)


def test_ยอด_18000_เข้าข้อ_6_5_ไล่ระดับ_3_แล้ว_5():
    p = plan(Decimal("18000"))
    assert p["clause"] == "6.5"
    assert [s["required_level"] for s in p["steps"]] == [3, 5]


def test_ยอดเล็กเข้าข้อ_6_7_ขั้นเดียว():
    assert plan(Decimal("1800"))["clause"] == "6.7"
    assert [s["required_level"] for s in plan(Decimal("1800"))["steps"]] == [3]


def test_ยอด_30000_เข้าข้อ_6_4_สามขั้น():
    p = plan(Decimal("30000"))
    assert p["clause"] == "6.4"
    assert [s["required_level"] for s in p["steps"]] == [3, 5, 8]


def test_ระดับถึง_TOA_อนุมัติได้_ผู้เบิกอนุมัติเองไม่ได้():
    p = plan(Decimal("18000"))
    assert cl.can_approve(p, PEOPLE, MAPS, AE, BANK, step=1)
    assert not cl.can_approve(p, PEOPLE, MAPS, AE, AE, step=1)


def test_หัวหน้ากดขั้นแรกได้_ขั้นถัดไปต้องระดับถึง():
    p = plan(Decimal("18000"))          # ระดับ 3 → 5
    assert cl.can_approve(p, PEOPLE, MAPS, AE, PU, step=1)
    assert not cl.can_approve(p, PEOPLE, MAPS, AE, PU, step=2)
    assert cl.can_approve(p, PEOPLE, MAPS, AE, BANK, step=2)
    p = plan(Decimal("30000"))          # ระดับ 3 → 5 → 8
    assert cl.can_approve(p, PEOPLE, MAPS, AE, BANK, step=2)
    assert not cl.can_approve(p, PEOPLE, MAPS, AE, BANK, step=3)
    assert cl.can_approve(p, PEOPLE, MAPS, AE, PONG, step=3)


def test_ระดับสูงกว่าอนุมัติแทนได้():
    assert cl.can_approve(plan(Decimal("1800")), PEOPLE, MAPS, AE, BANK, step=1)


def test_ยอดที่ต้องสูงกว่าหัวหน้ามีขั้น_2_ยอดเล็กจบขั้นเดียว():
    assert cl.next_step(plan(Decimal("18000")), 1) == 2
    assert cl.next_step(plan(Decimal("18000")), 2) is None
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
