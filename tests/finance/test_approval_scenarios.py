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
    return {"employee_id": eid, "id": uid, "level": level, "department_id": dept, "active": active}


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

@pytest.mark.parametrize("amount,clause,steps", [
    (1500, "6.7", 2),      # TOA ขอแค่ lv2 แต่ทุกใบต้องมีขั้น 2 (19/10/2026)
    (3000, "6.6", 2),
    (15000, "6.5", 2),
    (60000, "6.4", 2),
    (150000, "6.3", 2),
    (900000, "6.1", 2),
])
def test_staff_step_count_by_amount(amount, clause, steps):
    info = plan(STAFF, amount)
    assert info["clause"] == clause
    assert len(info["steps"]) == steps


def test_even_the_smallest_tier_needs_a_second_approval():
    """ผู้ใช้สั่ง 19/10/2026: ยอดเล็กแค่ไหนก็ต้องมีคนเหนือขั้น 1 อนุมัติอีกชั้น"""
    info = plan(STAFF, 1500)
    assert [s["required_level"] for s in info["steps"]] == [2, 3]
    assert "ขั้นที่สอง" in info["steps"][1]["label"]


def test_executive_borrowing_a_large_sum_needs_only_one_approval():
    """ผู้บริหาร lv8 เบิก 150,000 → ขั้น 1 ต้องการ lv9 ซึ่งเท่ากับ TOA พอดี จึงเหลือขั้นเดียว

    ไม่ใช่บั๊ก แต่เป็นจุดที่ควรรู้: ยอดใหญ่ของผู้บริหารผ่านการอนุมัติครั้งเดียว
    """
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
    """ยอด 3,000 (ข้อ 6.6 ต้อง lv3) หัวหน้า lv3 กดขั้น 1 → ข้ามขั้น 2 เองเพราะระดับถึงแล้ว"""
    info = plan(STAFF, 3000)
    d = rules.decide(info, ORG, NO_MAP, "STAFF", "LEAD", current_level=1, round_logs=[])
    assert d["allowed"] is True
    assert d["outcome"] == rules.OUTCOME_APPROVED
    assert d["skipped"] == [2]


def test_two_steps_when_the_leader_is_not_senior_enough():
    """ยอด 15,000 (ข้อ 6.5 ต้อง lv5) หัวหน้า lv3 กดได้แค่ขั้น 1 แล้วต้องส่งต่อ"""
    info = plan(STAFF, 15000)
    d = rules.decide(info, ORG, NO_MAP, "STAFF", "LEAD", current_level=1, round_logs=[])
    assert (d["outcome"], d["next_step"]) == (rules.OUTCOME_STEP, 2)

    logs = [approved_log(1, 1, LEAD["id"])]
    again = rules.decide(info, ORG, NO_MAP, "STAFF", "LEAD", current_level=2, round_logs=logs)
    assert again["allowed"] is False, "คนเดิมกดขั้น 2 ซ้ำไม่ได้"

    other = rules.decide(info, ORG, NO_MAP, "STAFF", "MGR", current_level=2, round_logs=logs)
    assert other["allowed"] is True
    assert other["outcome"] == rules.OUTCOME_APPROVED


def test_a_manager_taking_step_one_closes_the_whole_form_alone():
    """จุดแปลกที่ควรรู้: ถ้าผู้จัดการ lv5 ลงมากดขั้น 1 เอง ใบ 15,000 จบด้วยลายเซ็นคนเดียว"""
    info = plan(STAFF, 15000)
    d = rules.decide(info, ORG, NO_MAP, "STAFF", "MGR", current_level=1, round_logs=[])
    assert d["outcome"] == rules.OUTCOME_APPROVED
    assert d["skipped"] == [2]


def test_second_step_blocks_a_level_that_is_too_low():
    info = plan(STAFF, 15000)
    logs = [approved_log(1, 1, LEAD["id"])]
    d = rules.decide(info, ORG, NO_MAP, "STAFF", "LEAD2", current_level=2, round_logs=logs)
    assert d["allowed"] is False, "LEAD2 ระดับ 3 ยังไม่ถึง lv5 ของขั้น 2"


def test_resubmitting_clears_the_exclusion_so_the_same_lead_can_approve_again():
    """ตีกลับแล้วส่งใหม่ = รอบใหม่ หัวหน้าคนเดิมกลับมากดขั้น 1 ได้"""
    info = plan(STAFF, 15000)
    logs = [approved_log(1, 1, LEAD["id"]),
            {"id": 2, "level_no": 0, "action": rules.MARKER_RETURNED, "action_by": None},
            {"id": 3, "level_no": 0, "action": rules.MARKER_RESUBMITTED, "action_by": None}]
    fresh = rules.current_round(logs)
    assert rules.excluded_approver(fresh, 2) == frozenset()
    d = rules.decide(info, ORG, NO_MAP, "STAFF", "LEAD", current_level=1, round_logs=fresh)
    assert d["allowed"] is True


def test_markers_are_never_counted_as_approvals():
    logs = [{"id": 1, "level_no": 0, "action": rules.ACTION_APPROVED, "action_by": 99}]
    assert rules.step_approvals(logs) == []
    assert rules.excluded_approver(logs, 2) == frozenset()


# ---------------------------- ใครได้อีเมล ----------------------------

def test_email_stops_at_manager_while_executives_keep_the_right_to_approve():
    info = plan(STAFF, 3000)
    required = info["steps"][0]["required_level"]
    el = rules.eligible_approvers(ORG.values(), STAFF, required, NO_MAP, ())
    mailed = {p["employee_id"] for p in rules.notify_approvers(el, required)}
    assert mailed == {"LEAD", "LEAD2", "MGR"}
    assert {"CL", "DCEO"} <= {p["employee_id"] for p in el}


def test_a_step_that_only_executives_can_do_still_emails_them():
    info = plan(STAFF, 150000)
    last = info["steps"][-1]["required_level"]
    el = rules.eligible_approvers(ORG.values(), STAFF, last, NO_MAP, ())
    assert {p["employee_id"] for p in rules.notify_approvers(el, last)} == {"DCEO"}


def test_the_excluded_approver_is_not_emailed_for_the_next_step():
    info = plan(STAFF, 15000)
    logs = [approved_log(1, 1, MGR["id"])]
    state = rules.evaluate_step(info, ORG, NO_MAP, "STAFF", 2, logs)
    el = rules.eligible_approvers(ORG.values(), STAFF, state["required_level"], NO_MAP, state["excluded"])
    assert "MGR" not in {p["employee_id"] for p in rules.notify_approvers(el, state["required_level"])}
