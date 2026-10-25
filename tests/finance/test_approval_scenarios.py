# -*- coding: utf-8 -*-
"""เดินเรื่องจริงของผู้เบิกทีละเคส — ใช้ชั้นวงเงินชุดเดียวกับ prod (fin_approval_tiers 19/10/2026)

ไล่ตั้งแต่ยื่น ผ่านขั้น ข้ามขั้น ตีกลับ ส่งใหม่ จนถึงใครได้อีเมล
เป้าหมายคือจับ "จุดแปลก" ที่กฎเขียนไว้แล้วแต่ไม่มีใครเคยลองเดิน
"""
from decimal import Decimal

import pytest

from services.finance import approval_logic as rules
from services.finance.advance_logic import AdvanceRuleError

TIERS = [
    {"clause": "6.7", "amount_max": Decimal("2000"), "min_level": 2,
     "approver_label": "Asst. Sup (ระดับ 2)", "sort_order": 1},
    {"clause": "6.6", "amount_max": Decimal("5000"), "min_level": 3,
     "approver_label": "Sup / Asst.M (ระดับ 3–4)", "sort_order": 2},
    {"clause": "6.5", "amount_max": Decimal("20000"), "min_level": 5,
     "approver_label": "MGR / SM / DPCL (ระดับ 5–7)", "sort_order": 3},
    {"clause": "6.4", "amount_max": Decimal("100000"), "min_level": 8,
     "approver_label": "CL (ระดับ 8)", "sort_order": 4},
    {"clause": "6.3", "amount_max": Decimal("500000"), "min_level": 9,
     "approver_label": "DCEO (นโยบายระดับ 9)", "sort_order": 5},
    {"clause": "6.1", "amount_max": None, "min_level": 9,
     "approver_label": "ExC", "sort_order": 7},
]

DEPT, OTHER = 10, 20


def person(eid, uid, level, dept=DEPT, active=True):
    return {"employee_id": eid, "id": uid, "level": level, "department_id": dept, "division_id": dept, "active": active}


STAFF = person("STAFF", 1, 1)
LEAD = person("LEAD", 2, 3)
LEAD2 = person("LEAD2", 3, 3)
MGR = person("MGR", 4, 5)
CL = person("CL", 5, 8)
DCEO = person("DCEO", 6, 9, dept=OTHER)      # ระดับ 9 ข้ามแผนกได้ทั้งองค์กร
STRANGER = person("STRANGER", 7, 6, dept=OTHER)
ORG = {p["employee_id"]: p for p in (STAFF, LEAD, LEAD2, MGR, CL, DCEO, STRANGER)}
NO_MAP = {}


def plan(requester, amount, tiers=TIERS):
    return rules.evaluate(tiers, ORG, NO_MAP, requester["employee_id"], amount)


def approved_log(log_id, step, action_by):
    return {"id": log_id, "level_no": step, "action": rules.ACTION_APPROVED,
            "action_by": action_by, "action_at": None, "remark": None}


# ---------------------------- จำนวนขั้นตามยอดและระดับผู้เบิก ----------------------------

@pytest.mark.parametrize("amount,clause,level", [
    (1500, "6.7", 2),      # ขั้นเดียวตาม TOA (ผู้ใช้สั่ง 21/10/2026)
    (3000, "6.6", 3),
    (15000, "6.5", 5),
    (60000, "6.4", 8),     # ตรงไประดับ 8 ไม่ต้องผ่านหัวหน้าก่อน
    (150000, "6.3", 9),
    (900000, "6.1", 9),
])
def test_staff_one_step_at_the_toa_level(amount, clause, level):
    info = plan(STAFF, amount)
    assert info["clause"] == clause
    assert [s["required_level"] for s in info["steps"]] == [level]


def test_executive_borrowing_a_large_sum_needs_only_one_approval():
    """ผู้บริหาร lv8 เบิก 150,000 → ขั้นเดียวระดับ 9"""
    info = plan(CL, 150000)
    assert len(info["steps"]) == 1
    assert info["steps"][0]["required_level"] == 9


def test_level_9_requester_still_needs_someone_else():
    """ผู้เบิกระดับ 9 ยังต้องมีคนอนุมัติ และคนนั้นต้องไม่ใช่ตัวเอง"""
    boss = person("BOSS", 8, 9, dept=OTHER)
    org = {**ORG, "BOSS": boss}
    info = rules.evaluate(TIERS, org, NO_MAP, "BOSS", 400000)
    el = rules.eligible_approvers(org.values(), boss, info["steps"][-1]["required_level"], NO_MAP, ())
    assert [p["employee_id"] for p in el] == ["DCEO"]


def test_no_approver_at_all_is_refused_at_submit():
    """แผนกที่ไม่มีหัวหน้าเลย ต้องฟ้องตั้งแต่ยื่น ไม่ใช่ปล่อยใบค้าง"""
    lonely = person("LONELY", 9, 1, dept=99)
    with pytest.raises(AdvanceRuleError):
        rules.evaluate(TIERS, {"LONELY": lonely}, NO_MAP, "LONELY", 3000)


# ---------------------------- ใครอนุมัติได้ ----------------------------

def test_requester_can_never_approve_own_form():
    info = plan(LEAD, 3000)
    el = rules.eligible_approvers(ORG.values(), LEAD, info["steps"][0]["required_level"], NO_MAP, ())
    assert "LEAD" not in {p["employee_id"] for p in el}


def test_other_department_cannot_approve_unless_mapped_or_level_9():
    info = plan(STAFF, 3000)
    el = {p["employee_id"] for p in
          rules.eligible_approvers(ORG.values(), STAFF, info["steps"][0]["required_level"], NO_MAP, ())}
    assert "STRANGER" not in el          # แผนกอื่น ระดับ 6 แต่ไม่ได้ถูกแม็ป
    assert "DCEO" in el                  # ระดับ 9 ข้ามแผนกได้


def test_mapped_department_head_can_approve():
    el = {p["employee_id"] for p in
          rules.eligible_approvers(ORG.values(), STAFF, 2, {"STRANGER": {DEPT}}, ())}
    assert "STRANGER" in el


def test_inactive_person_drops_out():
    gone = person("GONE", 11, 7, active=False)
    el = {p["employee_id"] for p in
          rules.eligible_approvers([*ORG.values(), gone], STAFF, 2, NO_MAP, ())}
    assert "GONE" not in el


# ---------------------------- เดินเรื่อง ----------------------------

def test_lead_approving_a_small_form_finishes_it_in_one_click():
    """ยอด 3,000 (ข้อ 6.6 ต้อง lv3) หัวหน้า lv3 กดครั้งเดียวจบ"""
    info = plan(STAFF, 3000)
    d = rules.decide(info, ORG, NO_MAP, "STAFF", "LEAD", current_level=1, round_logs=[])
    assert (d["allowed"], d["outcome"], d["skipped"]) == (True, rules.OUTCOME_APPROVED, [])


def test_leader_below_toa_cannot_approve():
    """ยอด 15,000 (ข้อ 6.5 ต้อง lv5) หัวหน้า lv3 กดไม่ได้ ต้องเป็นผู้จัดการขึ้นไป"""
    info = plan(STAFF, 15000)
    assert rules.decide(info, ORG, NO_MAP, "STAFF", "LEAD", current_level=1, round_logs=[])["allowed"] is False
    d = rules.decide(info, ORG, NO_MAP, "STAFF", "MGR", current_level=1, round_logs=[])
    assert (d["allowed"], d["outcome"]) == (True, rules.OUTCOME_APPROVED)


def test_higher_level_may_approve_through_the_link():
    """ใครระดับสูงกว่า TOA เปิดลิงก์แล้วอนุมัติแทนได้ (ผู้ใช้สั่ง 21/10/2026)"""
    info = plan(STAFF, 15000)
    for who in ("CL", "DCEO"):
        d = rules.decide(info, ORG, NO_MAP, "STAFF", who, current_level=1, round_logs=[])
        assert (d["allowed"], d["outcome"]) == (True, rules.OUTCOME_APPROVED), who


def test_resubmitting_starts_a_new_round():
    """ตีกลับแล้วส่งใหม่ = รอบใหม่ ผู้จัดการคนเดิมกลับมากดได้"""
    info = plan(STAFF, 15000)
    logs = [approved_log(1, 1, MGR["id"]),
            {"id": 2, "level_no": 0, "action": rules.MARKER_RETURNED, "action_by": None},
            {"id": 3, "level_no": 0, "action": rules.MARKER_RESUBMITTED, "action_by": None}]
    fresh = rules.current_round(logs)
    assert fresh == []
    d = rules.decide(info, ORG, NO_MAP, "STAFF", "MGR", current_level=1, round_logs=fresh)
    assert d["allowed"] is True


def test_markers_are_never_counted_as_approvals():
    logs = [{"id": 1, "level_no": 0, "action": rules.ACTION_APPROVED, "action_by": 99}]
    assert rules.step_approvals(logs) == []
    assert rules.excluded_approver(logs, 2) == frozenset()


# ---------------------------- ใครได้อีเมล ----------------------------

def test_email_goes_to_the_toa_level_only_while_higher_levels_keep_the_right_to_approve():
    info = plan(STAFF, 3000)
    required = info["steps"][0]["required_level"]
    el = rules.eligible_approvers(ORG.values(), STAFF, required, NO_MAP, ())
    mailed = {p["employee_id"] for p in rules.notify_approvers(el, required)}
    assert mailed == {"LEAD", "LEAD2"}
    assert {"MGR", "CL", "DCEO"} <= {p["employee_id"] for p in el}


def test_a_step_that_only_executives_can_do_still_emails_them():
    info = plan(STAFF, 150000)
    last = info["steps"][-1]["required_level"]
    el = rules.eligible_approvers(ORG.values(), STAFF, last, NO_MAP, ())
    assert {p["employee_id"] for p in rules.notify_approvers(el, last)} == {"DCEO"}


def test_15000_emails_the_manager_not_the_lead():
    info = plan(STAFF, 15000)
    required = info["steps"][0]["required_level"]
    el = rules.eligible_approvers(ORG.values(), STAFF, required, NO_MAP, ())
    assert {p["employee_id"] for p in rules.notify_approvers(el, required)} == {"MGR"}
