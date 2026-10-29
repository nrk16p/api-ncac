"""ไล่ทุกขั้นตามชั้น TOA (ผู้ใช้สั่ง 26/10/2026) + กลไกขั้น/รอบอนุมัติ — pure rules."""
import pytest

from services.finance import approval_logic as A
from services.finance.advance_logic import AdvanceRuleError
from tests.finance.test_approval_logic import TIERS


def tier(clause):
    return next(t for t in TIERS if t["clause"] == clause)


def person(eid, uid, level, dept, active=True):
    return {"employee_id": eid, "id": uid, "level": level, "department_id": dept, "division_id": dept, "active": active}


def log(lid, level_no, action, action_by, at=None):
    return {"id": lid, "level_no": level_no, "action": action, "action_by": action_by, "action_at": at}


def levels(steps):
    return [s["required_level"] for s in steps]


def mk(*levels_):
    """steps หลายขั้นที่สร้างเอง ใช้ทดสอบกลไก (ระบบจริงตอนนี้มีขั้นเดียว)"""
    return [{"step": i + 1, "required_level": lv, "label": f"L{lv}"} for i, lv in enumerate(levels_)]


class TestPlanSteps:
    """ไล่ทุกขั้นตามชั้น TOA (ผู้ใช้สั่ง 26/10/2026 แทนสูงสุด 2 ขั้น): ทุกชั้น TOA ที่อยู่เหนือผู้เบิก
    ไล่ขึ้นไปจนถึงระดับที่ยอดนั้นต้องการ · ชั้น TOA ใน TIERS: 2, 3, 5, 8, 9"""

    @staticmethod
    def plan(level, amount):
        return levels(A.plan_steps(level, A.pick_tier(TIERS, amount), TIERS))

    @pytest.mark.parametrize("amount,expected", [
        ("1500", [3]),             # 610055: TOA 2 แต่ต้องสูงกว่าผู้เบิก → 3 ขั้นเดียว
        ("5000", [3]),
        ("15000", [3, 5]),         # พุทธชาติ (3) → เจษฎา (5)
        ("60000", [3, 5, 8]),      # → กอบชัย (8)
        ("300000", [3, 5, 8, 9]),
        ("99999999", [3, 5, 8, 9]),
    ])
    def test_level_2_requester(self, amount, expected):
        assert self.plan(2, amount) == expected

    def test_level_1_requester_climbs_every_toa_level(self):
        assert self.plan(1, "1500") == [2]
        assert self.plan(1, "15000") == [2, 3, 5]
        assert self.plan(1, "300000") == [2, 3, 5, 8, 9]

    @pytest.mark.parametrize("amount,expected", [
        ("1500", [4]),             # ต้องสูงกว่าผู้เบิก (3) → 4 ไม่มีชั้น TOA คั่น
        ("15000", [5]),            # ชั้นถัดไปคือ 5 = ชั้นที่ยอดต้องการพอดี
        ("60000", [5, 8]),         # ชั้น TOA ถัดจาก 3 คือ 5 ไม่ใช่ 4
        ("300000", [5, 8, 9]),
    ])
    def test_level_3_requester_follows_toa_levels_not_plus_one(self, amount, expected):
        assert self.plan(3, amount) == expected

    def test_level_5_requester(self):
        assert self.plan(5, "15000") == [6]
        assert self.plan(5, "60000") == [8]
        assert self.plan(5, "300000") == [8, 9]

    @pytest.mark.parametrize("amount", ["1500", "60000", "300000"])
    def test_level_8_and_9_are_one_step_at_9(self, amount):
        assert self.plan(8, amount) == [9]
        assert self.plan(9, amount) == [9]

    def test_labels_verbatim(self):
        assert A.plan_steps(2, tier("6.4"), TIERS) == [
            {"step": 1, "required_level": 3, "label": "หัวหน้าระดับ 3 ขึ้นไป (Sup / Asst.M (ระดับ 3–4))"},
            {"step": 2, "required_level": 5, "label": "หัวหน้าระดับ 5 ขึ้นไป (MGR / SM / DPCL (ระดับ 5–7))"},
            {"step": 3, "required_level": 8, "label": "ระดับ 8 ขึ้นไป (ข้อ 6.4 · CL (ระดับ 8))"},
        ]

    def test_one_step_keeps_the_toa_label(self):
        assert A.plan_steps(3, tier("6.5"), TIERS) == [
            {"step": 1, "required_level": 5, "label": "ระดับ 5 ขึ้นไป (ข้อ 6.5 · MGR / SM / DPCL (ระดับ 5–7))"},
        ]

    def test_future_tier_above_org_wide_is_still_one_step(self):
        assert levels(A.plan_steps(9, {**tier("6.1"), "min_level": 10}, TIERS)) == [10]

    def test_no_level(self):
        with pytest.raises(AdvanceRuleError, match=A.MSG_NO_LEVEL):
            A.plan_steps(None, tier("6.7"), TIERS)


class TestNextAfterApproval:
    TWO = mk(4, 5)
    BIG = mk(4, 8)
    ONE = A.plan_steps(5, tier("6.4"), TIERS)    # [8] — ผู้เบิกระดับ 5 ยอด 6.4 ขั้นเดียว

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
        assert A.next_after_approval(self.ONE, 1, 8) == (A.OUTCOME_APPROVED, None)
        assert A.skipped_steps(self.ONE, 1, 9) == []

    def test_unknown_level_never_skips(self):
        assert A.next_after_approval(self.TWO, 1, None) == (A.OUTCOME_STEP, 2)


class TestCurrentStepOf:
    @pytest.mark.parametrize("raw,count,expected", [
        (None, 2, 1), (0, 2, 1), (1, 2, 1), (2, 2, 2), (3, 2, 2), (2, 1, 1), ("x", 2, 1), ("2", 2, 2),
    ])
    def test_clamped(self, raw, count, expected):
        steps = mk(4, 5)[:count]
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
    def test_required_level_is_the_toa_step(self):
        res = ev("15000")
        assert (res["required_level"], levels(res["steps"]), res["direct_level"]) == (5, [5], 5)

    def test_no_approver_at_toa_level(self):
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
        assert (state["step"], state["total_steps"], state["required_level"], state["direct_level"]) == (1, 1, 5, 5)
        assert state["label"] == "ระดับ 5 ขึ้นไป (ข้อ 6.5 · MGR / SM / DPCL (ระดับ 5–7))"
        assert A.approval_tab(5, state["direct_level"]) == A.TAB_MINE
        assert A.approval_tab(8, state["direct_level"]) == A.TAB_DELEGABLE

    def test_leader_below_toa_cannot_approve(self):
        assert not A.can_approve(PEOPLE["L4"], REQ, 5, ())


class TestDecide:
    def test_toa_level_completes_in_one_click(self):
        d = A.decide(ev("15000"), PEOPLE, MAP, "R", "L5", 1, [])
        assert (d["allowed"], d["outcome"], d["next_step"], d["skipped"]) == (True, "approved", None, [])

    def test_leader_below_toa_denied(self):
        assert A.decide(ev("15000"), PEOPLE, MAP, "R", "L4", 1, [])["allowed"] is False

    def test_higher_level_may_approve_through_the_link(self):
        """ใครระดับสูงกว่า TOA เปิดลิงก์ใบแล้วอนุมัติแทนได้ (ผู้ใช้สั่ง 21/10/2026)"""
        for who in ("L5x", "L8", "N9"):
            d = A.decide(ev("15000"), PEOPLE, MAP, "R", who, 1, [])
            assert (d["allowed"], d["outcome"]) == (True, "approved"), who

    def test_60000_goes_to_level_5_first_then_level_8(self):
        """ไล่ขั้นตาม TOA (ผู้ใช้สั่ง 26/10/2026): ผู้เบิกระดับ 3 ขอ 60,000 → ระดับ 5 แล้วระดับ 8"""
        d = A.decide(ev("60000"), PEOPLE, MAP, "R", "L5", 1, [])
        assert (d["allowed"], d["outcome"], d["next_step"]) == (True, "step", 2)
        logs = [log(1, 1, "APPROVED", PEOPLE["L5"]["id"])]
        assert A.decide(ev("60000"), PEOPLE, MAP, "R", "L5", 2, logs)["allowed"] is False
        assert A.decide(ev("60000"), PEOPLE, MAP, "R", "L8", 2, logs)["outcome"] == "approved"

    def test_level_8_may_take_step_1_and_skip_step_2(self):
        d = A.decide(ev("60000"), PEOPLE, MAP, "R", "L8", 1, [])
        assert (d["allowed"], d["outcome"], d["skipped"]) == (True, "approved", [2])

    def test_requester_and_strangers_denied(self):
        assert A.decide(ev("15000"), PEOPLE, MAP, "R", "R", 1, [])["allowed"] is False
        assert A.decide(ev("15000"), PEOPLE, MAP, "R", "NOPE", 1, [])["allowed"] is False

    def test_old_two_step_document_clamps_to_the_single_step(self):
        """ใบที่ค้าง current_level = 2 จากกติกาเดิม ต้องหนีบกลับมาขั้นเดียว และคนระดับ TOA กดจบได้"""
        logs = [log(1, 1, "APPROVED", PEOPLE["L4"]["id"])]
        d = A.decide(ev("15000"), PEOPLE, MAP, "R", "L5", 2, logs)
        assert (d["step"], d["total_steps"], d["allowed"], d["outcome"]) == (1, 1, True, "approved")
