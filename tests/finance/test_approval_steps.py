"""v3 §5 two-step approval (หัวหน้าถัดไป → TOA) + approval rounds — pure rules."""
import pytest

from services.finance import approval_logic as A
from services.finance.advance_logic import AdvanceRuleError
from tests.finance.test_approval_logic import TIERS


def tier(clause):
    return next(t for t in TIERS if t["clause"] == clause)


def person(eid, uid, level, dept, active=True):
    return {"employee_id": eid, "id": uid, "level": level, "department_id": dept, "active": active}


def log(lid, level_no, action, action_by, at=None):
    return {"id": lid, "level_no": level_no, "action": action, "action_by": action_by, "action_at": at}


def levels(steps):
    return [s["required_level"] for s in steps]


class TestPlanSteps:
    @pytest.mark.parametrize("amount,expected", [
        ("1500", [4]),         # 6.7: max(2, 4) = 4, not > 4 → step 1 only
        ("15000", [4, 5]),     # 6.5
        ("60000", [4, 8]),     # 6.4
    ])
    def test_spec_examples_requester_level_3(self, amount, expected):
        assert levels(A.plan_steps(3, A.pick_tier(TIERS, amount))) == expected

    def test_labels_verbatim(self):
        steps = A.plan_steps(3, tier("6.5"))
        assert steps == [
            {"step": 1, "required_level": 4, "label": "หัวหน้าระดับ 4 ขึ้นไป"},
            {"step": 2, "required_level": 5, "label": "ระดับ 5 ขึ้นไป (ข้อ 6.5 · MGR / SM / DPCL (ระดับ 5–7))"},
        ]

    def test_review_focus_level_5_asks_15000_is_one_step(self):
        steps = A.plan_steps(5, tier("6.5"))  # step 1 = 6; step 2 = max(5, 6) = 6, not > 6
        assert steps == [{"step": 1, "required_level": 6, "label": "หัวหน้าระดับ 6 ขึ้นไป"}]

    @pytest.mark.parametrize("clause", ["6.7", "6.5", "6.3", "6.1"])
    def test_level_9_requester_is_one_step_at_9(self, clause):
        assert levels(A.plan_steps(9, tier(clause))) == [9]

    def test_level_8_requester_big_amount_one_step_at_9(self):
        assert levels(A.plan_steps(8, tier("6.4"))) == [9]

    def test_step_2_is_capped_like_v2(self):
        # requester+1 = 10 never becomes a step (v2 rule caps at the org-wide level)
        assert levels(A.plan_steps(9, tier("6.3"))) == [9]

    def test_future_tier_above_org_wide_is_a_second_step(self):
        assert levels(A.plan_steps(9, {**tier("6.1"), "min_level": 10})) == [9, 10]

    def test_no_level(self):
        with pytest.raises(AdvanceRuleError, match=A.MSG_NO_LEVEL):
            A.plan_steps(None, tier("6.7"))


class TestNextAfterApproval:
    TWO = A.plan_steps(3, tier("6.5"))    # [4, 5]
    BIG = A.plan_steps(3, tier("6.4"))    # [4, 8]
    ONE = A.plan_steps(3, tier("6.7"))    # [4]

    def test_step_1_moves_on(self):
        assert A.next_after_approval(self.TWO, 1, 4) == (A.OUTCOME_STEP, 2)
        assert A.skipped_steps(self.TWO, 1, 4) == []

    def test_dynamic_skip_when_approver_level_reaches_step_2(self):
        assert A.next_after_approval(self.TWO, 1, 5) == (A.OUTCOME_APPROVED, None)
        assert A.skipped_steps(self.TWO, 1, 5) == [2]
        assert A.next_after_approval(self.BIG, 1, 9) == (A.OUTCOME_APPROVED, None)

    def test_no_skip_just_below(self):
        assert A.next_after_approval(self.BIG, 1, 7) == (A.OUTCOME_STEP, 2)

    def test_last_step_finishes(self):
        assert A.next_after_approval(self.TWO, 2, 5) == (A.OUTCOME_APPROVED, None)
        assert A.skipped_steps(self.TWO, 2, 9) == []

    def test_single_step_finishes(self):
        assert A.next_after_approval(self.ONE, 1, 4) == (A.OUTCOME_APPROVED, None)

    def test_unknown_level_never_skips(self):
        assert A.next_after_approval(self.TWO, 1, None) == (A.OUTCOME_STEP, 2)


class TestCurrentStepOf:
    @pytest.mark.parametrize("raw,count,expected", [
        (None, 2, 1), (0, 2, 1), (1, 2, 1), (2, 2, 2), (3, 2, 2), (2, 1, 1), ("x", 2, 1), ("2", 2, 2),
    ])
    def test_clamped(self, raw, count, expected):
        steps = A.plan_steps(3, tier("6.5"))[:count]
        assert A.current_step_of(steps, raw) == expected


class TestRounds:
    def test_no_marker_is_all_logs(self):
        logs = [log(2, 1, "APPROVED", 20), log(1, 1, "SUBMITTED", 10)]
        assert [l["id"] for l in A.current_round(logs)] == [1, 2]

    def test_only_logs_after_latest_resubmitted(self):
        logs = [log(1, 1, "APPROVED", 20), log(2, 2, "APPROVED", 30), log(3, 0, "RETURNED", 90),
                log(4, 0, "RESUBMITTED", 10), log(5, 1, "APPROVED", 21), log(6, 0, "RETURNED", 90),
                log(7, 0, "RESUBMITTED", 10), log(8, 1, "APPROVED", 22)]
        assert [l["id"] for l in A.current_round(logs)] == [8]

    def test_marker_last_means_empty_round(self):
        logs = [log(1, 1, "APPROVED", 20), log(2, 0, "RETURNED", 90), log(3, 0, "RESUBMITTED", 10)]
        assert A.current_round(logs) == []

    def test_excluded_is_step_1_approver_at_step_2_only(self):
        logs = [log(1, 1, "APPROVED", 20)]
        assert A.excluded_approver(logs, 1) == frozenset()
        assert A.excluded_approver(logs, 2) == frozenset({20})

    def test_markers_and_rejects_never_count_as_approvals(self):
        logs = [log(1, 0, "RETURNED", 90), log(2, 0, "RESUBMITTED", 10), log(3, 0, "APPROVED", 91),
                log(4, 1, "REJECTED", 92)]
        assert A.excluded_approver(logs, 2) == frozenset()
        assert A.step_approvals(logs) == []

    def test_round_1_approver_not_excluded_in_round_2(self):
        logs = [log(1, 1, "APPROVED", 20), log(2, 2, "APPROVED", 30), log(3, 0, "RETURNED", 90),
                log(4, 0, "RESUBMITTED", 10), log(5, 1, "APPROVED", 21)]
        assert A.excluded_approver(A.current_round(logs), 2) == frozenset({21})

    def test_step_approvals_current_round_earliest_per_step(self):
        logs = [log(1, 1, "APPROVED", 20, "t1"), log(2, 2, "APPROVED", 30, "t2"), log(3, 0, "RESUBMITTED", 10),
                log(5, 2, "APPROVED", 31, "t5"), log(4, 1, "APPROVED", 21, "t4"), log(6, 2, "APPROVED", 32, "t6")]
        assert A.step_approvals(A.current_round(logs)) == [
            {"step": 1, "action_by": 21, "action_at": "t4"},
            {"step": 2, "action_by": 31, "action_at": "t5"},
        ]


REQ = person("R", 1, 3, 10)                     # requester level 3, dept 10
PEOPLE = {p["employee_id"]: p for p in [
    REQ,
    person("L4", 2, 4, 10),                     # หัวหน้าถัดไป, same dept
    person("L4b", 3, 4, 10),
    person("L5", 4, 5, 10),                     # MGR same dept
    person("L5x", 5, 5, 20),                    # MGR other dept, mapped to 10
    person("L8", 6, 8, 30),                     # CL mapped to 10
    person("N9", 7, 9, 99),                     # org-wide
]}
MAP = {"L5x": {10}, "L8": {10}}


def ev(amount, requester="R", people=PEOPLE):
    return A.evaluate(TIERS, people, MAP, requester, amount)


class TestEvaluateSteps:
    def test_required_level_is_final_step(self):
        res = ev("15000")
        assert (res["required_level"], levels(res["steps"]), res["direct_level"]) == (5, [4, 5], 5)

    def test_no_final_step_approver(self):
        people = {k: v for k, v in PEOPLE.items() if k in ("R", "L4")}
        with pytest.raises(AdvanceRuleError, match=A.MSG_NO_APPROVER):
            ev("15000", people=people)

    def test_level_9_requester_needs_another_9(self):
        people = {**PEOPLE, "R9": person("R9", 8, 9, 10)}
        res = A.evaluate(TIERS, people, MAP, "R9", "100")
        assert levels(res["steps"]) == [9]
        assert not A.can_approve(people["R9"], people["R9"], 9, ())
        assert A.can_approve(people["N9"], people["R9"], 9, ())
        state = A.evaluate_step(res, people, MAP, "R9", 1)
        assert state["direct_level"] == 9

    def test_step_1_direct_level_and_tab(self):
        state = A.evaluate_step(ev("15000"), PEOPLE, MAP, "R", 1)
        assert (state["step"], state["total_steps"], state["required_level"], state["direct_level"]) == (1, 2, 4, 4)
        assert state["label"] == "หัวหน้าระดับ 4 ขึ้นไป"
        assert A.approval_tab(4, state["direct_level"]) == A.TAB_MINE
        assert A.approval_tab(5, state["direct_level"]) == A.TAB_DELEGABLE

    def test_step_2_direct_level_skips_excluded(self):
        # L5 (users.id 4) somehow approved step 1 → at step 2 only L5x remains at level 5
        state = A.evaluate_step(ev("15000"), PEOPLE, MAP, "R", 2, [log(1, 1, "APPROVED", 4)])
        assert state["excluded"] == frozenset({4}) and state["direct_level"] == 5
        assert not A.can_approve(PEOPLE["L5"], REQ, 5, (), state["excluded"])
        assert A.can_approve(PEOPLE["L5x"], REQ, 5, MAP["L5x"], state["excluded"])


class TestDecide:
    def test_step_1_leader_moves_to_step_2(self):
        d = A.decide(ev("15000"), PEOPLE, MAP, "R", "L4", 1, [])
        assert (d["allowed"], d["step"], d["outcome"], d["next_step"], d["skipped"]) == (True, 1, "step", 2, [])

    def test_step_1_by_toa_level_completes(self):
        d = A.decide(ev("15000"), PEOPLE, MAP, "R", "L5", 1, [])
        assert (d["allowed"], d["outcome"], d["next_step"], d["skipped"]) == (True, "approved", None, [2])

    def test_step_1_approver_never_approves_step_2(self):
        logs = [log(1, 1, "APPROVED", PEOPLE["L8"]["id"])]
        d = A.decide(ev("60000"), PEOPLE, MAP, "R", "L8", 2, logs)
        assert d["allowed"] is False
        assert A.decide(ev("60000"), PEOPLE, MAP, "R", "N9", 2, logs)["allowed"] is True

    def test_step_2_below_level_denied(self):
        logs = [log(1, 1, "APPROVED", PEOPLE["L4"]["id"])]
        assert A.decide(ev("15000"), PEOPLE, MAP, "R", "L4b", 2, logs)["allowed"] is False
        d = A.decide(ev("15000"), PEOPLE, MAP, "R", "L5", 2, logs)
        assert (d["allowed"], d["outcome"]) == (True, "approved")

    def test_round_1_step_1_approver_may_approve_step_2_in_round_2(self):
        logs = [log(1, 1, "APPROVED", PEOPLE["L5x"]["id"]), log(2, 0, "RETURNED", 90),
                log(3, 0, "RESUBMITTED", 1), log(4, 1, "APPROVED", PEOPLE["L4"]["id"])]
        assert A.decide(ev("15000"), PEOPLE, MAP, "R", "L5x", 2, A.current_round(logs))["allowed"] is True
        # ...but the round-2 step-1 approver may not
        assert A.decide(ev("15000"), PEOPLE, MAP, "R", "L4", 2, A.current_round(logs))["allowed"] is False

    def test_requester_and_strangers_denied(self):
        assert A.decide(ev("15000"), PEOPLE, MAP, "R", "R", 1, [])["allowed"] is False
        assert A.decide(ev("15000"), PEOPLE, MAP, "R", "NOPE", 1, [])["allowed"] is False

    def test_plan_shrank_clamps_to_last_step(self):
        d = A.decide(ev("1500"), PEOPLE, MAP, "R", "L4", 2, [])
        assert (d["step"], d["total_steps"], d["allowed"], d["outcome"]) == (1, 1, True, "approved")
