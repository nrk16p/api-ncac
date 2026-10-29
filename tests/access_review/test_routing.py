"""Approval routing on submit — pure plan_route + compute_plan against the SQLite org."""
from services.access_review import logic as L


def test_level_rank_is_case_insensitive_and_unknown_is_none():
    assert L.level_rank("C-level") == 8
    assert L.level_rank("  deputy c-LEVEL ") == 7
    assert L.level_rank("Officer") == 1
    assert L.level_rank("Intern") is None
    assert L.level_rank(None) is None


def test_staff_goes_to_lv5_6_in_division():
    plan = L.plan_route("R", 3, [("R", 3), ("M5", 5), ("M6", 6), ("S4", 4), ("C7", 7)], ["DIV"])
    assert plan.route == L.ROUTE_DEPT_HEAD
    assert plan.approvers == ["M5", "M6"]


def test_staff_without_lv5_6_falls_back_to_division_approvers():
    plan = L.plan_route("R", 2, [("R", 2), ("S4", 4)], ["DIV1", "DIV2"])
    assert plan == L.RoutePlan(L.ROUTE_DIVISION, ["DIV1", "DIV2"])


def test_staff_without_anyone_goes_to_it_queue():
    assert L.plan_route("R", 1, [("R", 1)], []) == L.RoutePlan(L.ROUTE_IT_QUEUE, [])


def test_unknown_level_is_treated_as_staff():
    assert L.plan_route("R", None, [("M5", 5)], ["DIV"]).route == L.ROUTE_DEPT_HEAD


def test_manager_goes_to_division_approvers_not_peers():
    plan = L.plan_route("R", 5, [("M6", 6)], ["DIV"])
    assert plan == L.RoutePlan(L.ROUTE_DIVISION, ["DIV"])


def test_manager_without_division_approvers_goes_to_it_queue():
    assert L.plan_route("R", 6, [("M5", 5)], []) == L.RoutePlan(L.ROUTE_IT_QUEUE, [])


def test_executive_bypasses():
    assert L.plan_route("R", 7, [("M5", 5)], ["DIV"]) == L.RoutePlan(L.ROUTE_BYPASS, [])
    assert L.plan_route("R", 9, [], []).route == L.ROUTE_BYPASS


def test_requester_is_never_an_approver():
    # Lv.6 requester listed as a division approver of their own division
    assert L.plan_route("R", 6, [], ["R"]) == L.RoutePlan(L.ROUTE_IT_QUEUE, [])
    assert L.plan_route("R", 6, [], ["R", "DIV"]).approvers == ["DIV"]
    # staff fallback list containing the requester
    assert L.plan_route("R", 3, [], ["R"]).route == L.ROUTE_IT_QUEUE


# ---------------------------- compute_plan (DB) ----------------------------

def test_compute_plan_uses_active_heads_in_same_division_only(db, org):
    req = org.user("R", 3, 1)
    org.user("HEAD", 5, 2)                    # same division (10), other department → counts
    org.user("GONE", 6, 1, status="Inactive")  # inactive → ignored
    org.user("OTHER", 5, 3)                   # division 20 → ignored
    org.division_approver("DIV", 10)
    plan = L.compute_plan(db, req)
    assert plan == L.RoutePlan(L.ROUTE_DEPT_HEAD, ["HEAD"])


def test_compute_plan_staff_falls_back_to_form_approver_divisions(db, org):
    req = org.user("R", 2, 1)
    org.user("DIV", 7, 3)
    org.user("OLD", 7, 3)
    org.division_approver("DIV", 10)
    org.division_approver("OLD", 10, active=False)
    org.division_approver("X", 20)
    assert L.compute_plan(db, req) == L.RoutePlan(L.ROUTE_DIVISION, ["DIV"])


def test_compute_plan_staff_to_it_queue(db, org):
    req = org.user("R", 1, 1)
    assert L.compute_plan(db, req) == L.RoutePlan(L.ROUTE_IT_QUEUE, [])


def test_compute_plan_manager_uses_division_table_and_excludes_self(db, org):
    req = org.user("R", 5, 1)
    org.user("PEER", 6, 2)
    org.user("DIV", 8, 3)
    org.division_approver("R", 10)
    org.division_approver("DIV", 10)
    assert L.compute_plan(db, req) == L.RoutePlan(L.ROUTE_DIVISION, ["DIV"])


def test_compute_plan_manager_without_division_approver(db, org):
    req = org.user("R", 6, 1)
    org.user("PEER", 5, 2)
    assert L.compute_plan(db, req) == L.RoutePlan(L.ROUTE_IT_QUEUE, [])


def test_compute_plan_executive_bypass(db, org):
    req = org.user("R", 7, 1)
    org.user("HEAD", 5, 1)
    assert L.compute_plan(db, req).route == L.ROUTE_BYPASS
