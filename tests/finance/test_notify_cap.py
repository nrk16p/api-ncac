# -*- coding: utf-8 -*-
"""อีเมลส่งเฉพาะคนระดับที่ TOA กำหนด (ผู้ใช้สั่ง 21/10/2026) — สิทธิ์กดอนุมัติไม่เปลี่ยน"""
from services.finance import approval_logic as rules


def p(eid, level):
    return {"employee_id": eid, "id": int(eid), "level": level, "department_id": 11, "active": True}


ELIGIBLE = [p("1", 3), p("2", 3), p("3", 5), p("4", 8), p("5", 9)]


def test_only_the_lowest_eligible_level_gets_the_email():
    got = rules.notify_approvers(ELIGIBLE, required=2)
    assert [x["employee_id"] for x in got] == ["1", "2"]


def test_step_that_needs_a_high_level_mails_that_level():
    high = [p("4", 8), p("5", 9)]
    got = rules.notify_approvers(high, required=8)
    assert [x["level"] for x in got] == [8]


def test_nobody_exactly_at_toa_level_mails_the_next_band_up():
    got = rules.notify_approvers([p("5", 9), p("6", 9), p("7", 8)], required=5)
    assert [x["employee_id"] for x in got] == ["7"]


def test_people_below_the_required_level_are_never_mailed():
    got = rules.notify_approvers([p("1", 3), p("3", 5), p("4", 8)], required=5)
    assert [x["employee_id"] for x in got] == ["3"]


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
