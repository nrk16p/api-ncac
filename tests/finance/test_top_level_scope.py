"""ระดับ 9 แยกตามฝ่าย + เอา 660239 ออกจากผู้อนุมัติ (ผู้ใช้สั่ง 18/11/2026)"""
from services.finance import approval_logic as A

CEO = {"employee_id": "360002", "id": 1, "level": 9, "department_id": 1, "division_id": 29, "active": True}
DCEO = {"employee_id": "563328", "id": 2, "level": 9, "department_id": 1, "division_id": 29, "active": True}
BOARD = {"employee_id": "660239", "id": 3, "level": 9, "department_id": 1, "division_id": 29, "active": True}
OTHER9 = {"employee_id": "999999", "id": 4, "level": 9, "department_id": 1, "division_id": 29, "active": True}


def req(division, eid="700001", dept=50, level=5):
    return {"employee_id": eid, "id": 99, "level": level, "department_id": dept, "division_id": division, "active": True}


def ok(approver, requester):
    return A.can_approve(approver, requester, 9, ())


def test_operations_goes_to_dceo_only():
    for div in (15, 16, 17, 18, 19, 20, 22, 23):
        assert ok(DCEO, req(div)) and not ok(CEO, req(div))


def test_support_finance_goes_to_ceo_only():
    for div in (24, 25, 26, 27, 30, 32, 33, 34):
        assert ok(CEO, req(div)) and not ok(DCEO, req(div))


def test_executive_division_both_but_not_self():
    director = req(29, eid="670488", dept=1, level=8)
    assert ok(CEO, director) and ok(DCEO, director)
    assert ok(CEO, DCEO) and ok(DCEO, CEO)  # ใบของอีกคน
    assert not ok(CEO, CEO)


def test_uncovered_division_falls_back_to_all():
    for div in (14, 99, None):
        assert ok(CEO, req(div)) and ok(DCEO, req(div))


def test_unlisted_level9_stays_company_wide():
    assert ok(OTHER9, req(15)) and ok(OTHER9, req(24))


def test_board_member_never_approves():
    assert not ok(BOARD, req(15)) and not ok(BOARD, req(24)) and not ok(BOARD, req(None))
    assert not A.can_approve(BOARD, req(15), 2, (), named="660239")


def test_eligible_list_filters_by_division():
    people = [CEO, DCEO, BOARD]
    ids = lambda r: sorted(p["employee_id"] for p in A.eligible_approvers(people, r, 9, {}))
    assert ids(req(17)) == ["563328"]
    assert ids(req(25)) == ["360002"]
    assert ids(req(14)) == ["360002", "563328"]
