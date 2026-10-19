# -*- coding: utf-8 -*-
"""อีเมลหยุดที่ระดับผู้จัดการ (ผู้ใช้สั่ง 19/10/2026) — สิทธิ์กดอนุมัติไม่เปลี่ยน"""
from services.finance import approval_logic as rules


def p(eid, level):
    return {"employee_id": eid, "id": int(eid), "level": level, "department_id": 11, "active": True}


ELIGIBLE = [p("1", 3), p("2", 3), p("3", 5), p("4", 8), p("5", 9)]


def test_stops_at_manager_level():
    got = rules.notify_approvers(ELIGIBLE, required=2)
    assert [x["level"] for x in got] == [3, 3, 5]


def test_step_that_needs_a_high_level_keeps_its_own_floor():
    """ขั้นที่ต้องการระดับ 8 กดเพดานลงมาต่ำกว่านั้นไม่ได้"""
    high = [p("4", 8), p("5", 9)]
    got = rules.notify_approvers(high, required=8)
    assert [x["level"] for x in got] == [8]


def test_falls_back_to_the_lowest_band_when_the_cap_clears_everyone():
    got = rules.notify_approvers([p("5", 9), p("6", 9), p("7", 8)], required=2)
    assert [x["employee_id"] for x in got] == ["7"]


def test_empty_stays_empty():
    assert rules.notify_approvers([], required=2) == []


def test_missing_level_is_not_notified_but_does_not_break():
    got = rules.notify_approvers([{"employee_id": "9", "id": 9, "level": None}, p("3", 5)], required=2)
    assert [x["employee_id"] for x in got] == ["3"]


def test_approval_rights_are_untouched():
    """ผู้บริหารยังกดอนุมัติได้เหมือนเดิม แค่ไม่ได้รับอีเมล"""
    requester = p("0", 1)
    people = ELIGIBLE + [requester]
    can = rules.eligible_approvers(people, requester, 2, {}, ())
    assert 9 in [x["level"] for x in can]
