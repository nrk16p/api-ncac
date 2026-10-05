from decimal import Decimal

import pytest

from services.finance import approval_logic as A
from services.finance.advance_logic import AdvanceRuleError

TIERS = [
    {"clause": "6.7", "amount_max": Decimal("2000.00"), "min_level": 2, "approver_label": "Asst. Sup (ระดับ 2)", "sort_order": 1},
    {"clause": "6.6", "amount_max": Decimal("5000.00"), "min_level": 3, "approver_label": "Sup / Asst.M (ระดับ 3–4)", "sort_order": 2},
    {"clause": "6.5", "amount_max": Decimal("20000.00"), "min_level": 5, "approver_label": "MGR / SM / DPCL (ระดับ 5–7)", "sort_order": 3},
    {"clause": "6.4", "amount_max": Decimal("100000.00"), "min_level": 8, "approver_label": "CL (ระดับ 8)", "sort_order": 4},
    {"clause": "6.3", "amount_max": Decimal("500000.00"), "min_level": 9, "approver_label": "DCEO (นโยบายระดับ 9)", "sort_order": 5},
    {"clause": "6.2", "amount_max": Decimal("1000000.00"), "min_level": 9, "approver_label": "CEO", "sort_order": 6},
    {"clause": "6.1", "amount_max": None, "min_level": 9, "approver_label": "ExC", "sort_order": 7},
]


def person(eid, level, dept, active=True):
    return {"employee_id": eid, "id": hash(eid) % 10000, "level": level, "department_id": dept, "active": active}


class TestPickTier:
    @pytest.mark.parametrize("amount,clause", [
        ("0.01", "6.7"), ("2000", "6.7"), ("2000.00", "6.7"), ("2000.01", "6.6"), ("5000", "6.6"),
        ("20000", "6.5"), ("100000", "6.4"), ("500000", "6.3"), ("1000000", "6.2"), ("1000000.01", "6.1"),
        ("99999999", "6.1"),
    ])
    def test_boundaries_inclusive(self, amount, clause):
        assert A.pick_tier(TIERS, amount)["clause"] == clause

    def test_order_by_sort_order_not_list_order(self):
        assert A.pick_tier(list(reversed(TIERS)), "1500")["clause"] == "6.7"

    @pytest.mark.parametrize("bad", [None, "", "abc", 0, "0", -5, "NaN"])
    def test_bad_amount(self, bad):
        with pytest.raises(AdvanceRuleError, match=A.MSG_BAD_AMOUNT):
            A.pick_tier(TIERS, bad)

    def test_no_tiers(self):
        with pytest.raises(AdvanceRuleError, match=A.MSG_NO_TIERS):
            A.pick_tier([], "100")

    def test_no_unbounded_row_above_cap(self):
        with pytest.raises(AdvanceRuleError, match=A.MSG_NO_TIERS):
            A.pick_tier(TIERS[:2], "9000")


class TestRequiredLevel:
    def test_tier_above_requester(self):
        assert A.required_level(5, 1) == 5

    def test_requester_above_tier_bumps(self):
        assert A.required_level(2, 5) == 6  # Manager asks 1,500 → level 6+

    def test_level_9_requester_stays_9(self):
        assert A.required_level(2, 9) == 9

    def test_future_tier_above_org_wide_kept(self):
        assert A.required_level(10, 3) == 10

    def test_no_level(self):
        with pytest.raises(AdvanceRuleError, match=A.MSG_NO_LEVEL):
            A.required_level(2, None)


class TestCanApprove:
    REQ = {"employee_id": "R", "department_id": 3}

    def test_same_department_at_level(self):
        assert A.can_approve(person("X", 5, 3), self.REQ, 5, ())

    def test_below_required(self):
        assert not A.can_approve(person("X", 4, 3), self.REQ, 5, ())

    def test_other_department_unmapped(self):
        assert not A.can_approve(person("X", 8, 4), self.REQ, 5, ())

    def test_other_department_mapped(self):
        assert A.can_approve(person("X", 8, 1), self.REQ, 5, {3, 11})

    def test_level_9_org_wide(self):
        assert A.can_approve(person("CEO", 9, 1), self.REQ, 9, ())

    def test_self_never(self):
        assert not A.can_approve(person("R", 9, 3), self.REQ, 9, ())

    def test_inactive_never(self):
        assert not A.can_approve(person("X", 6, 3, active=False), self.REQ, 5, ())

    def test_no_level_never(self):
        assert not A.can_approve(person("X", None, 3), self.REQ, 1, ())


class TestDirectLevelAndTab:
    def test_lowest_eligible_level(self):
        people = [person("A", 5, 3), person("B", 6, 3), person("C", 9, 1)]
        req = {"employee_id": "R", "department_id": 3}
        assert A.direct_level(people, req, 5, {}) == 5
        assert A.approval_tab(5, 5) == A.TAB_MINE
        assert A.approval_tab(9, 5) == A.TAB_DELEGABLE

    def test_escalates_when_band_empty_in_department(self):
        people = [person("C", 9, 1)]  # dept 3 has nobody at 2+
        assert A.direct_level(people, {"employee_id": "R", "department_id": 3}, 2, {}) == 9

    def test_none_when_nobody(self):
        assert A.direct_level([person("R", 9, 1)], {"employee_id": "R", "department_id": 1}, 9, {}) is None


class TestEvaluate:
    PEOPLE = {
        "R": person("R", 5, 3),        # requester: Manager, dept 3
        "S6": person("S6", 6, 3),      # Senior Manager same dept
        "CL": person("CL", 8, 1),      # C-level mapped to dept 3
        "CEO": person("CEO", 9, 1),
        "CEO2": person("CEO2", 9, 1),
    }
    MAP = {"CL": {3}}

    def test_manager_asks_1500(self):
        res = A.evaluate(TIERS, self.PEOPLE, self.MAP, "R", "1500")
        assert res == {"clause": "6.7", "approver_label": "Asst. Sup (ระดับ 2)", "min_level": 2,
                       "required_level": 6, "direct_level": 6,
                       "steps": [{"step": 1, "required_level": 6, "label": "หัวหน้าระดับ 6 ขึ้นไป"}]}

    def test_150k_goes_to_level_9(self):
        res = A.evaluate(TIERS, self.PEOPLE, self.MAP, "R", "150000")
        assert (res["clause"], res["required_level"], res["direct_level"]) == ("6.3", 9, 9)

    def test_ceo_requester_needs_other_ceo(self):
        res = A.evaluate(TIERS, self.PEOPLE, self.MAP, "CEO", "100")
        assert res["required_level"] == 9 and res["direct_level"] == 9

    def test_unknown_requester(self):
        with pytest.raises(AdvanceRuleError, match=A.MSG_NO_LEVEL):
            A.evaluate(TIERS, self.PEOPLE, self.MAP, "NOPE", "100")

    def test_no_approver(self):
        people = {"R": person("R", 9, 1)}
        with pytest.raises(AdvanceRuleError, match=A.MSG_NO_APPROVER):
            A.evaluate(TIERS, people, {}, "R", "100")


class TestDirectApprovers:
    def test_only_lowest_eligible_level(self):
        req = person("R", 1, 10)
        people = [req, person("A", 3, 10), person("B", 5, 10), person("C", 3, 10)]
        got = A.direct_approvers(people, req, 2, {})
        assert sorted(p["employee_id"] for p in got) == ["A", "C"]

    def test_requester_excluded(self):
        req = person("R", 9, 10)
        people = [req, person("X", 9, 20)]
        assert [p["employee_id"] for p in A.direct_approvers(people, req, 9, {})] == ["X"]

    def test_level_9_is_org_wide(self):
        req = person("R", 4, 10)
        people = [req, person("N", 9, 99), person("D", 5, 10), person("E", 6, 20)]
        assert [p["employee_id"] for p in A.direct_approvers(people, req, 5, {})] == ["D"]
        assert [p["employee_id"] for p in A.direct_approvers([req, person("N", 9, 99)], req, 9, {})] == ["N"]

    def test_empty_when_nobody_eligible(self):
        req = person("R", 1, 10)
        assert A.direct_approvers([req, person("A", 1, 10), person("B", 5, 20, active=False)], req, 3, {}) == []
