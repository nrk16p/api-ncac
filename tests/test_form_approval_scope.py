"""Form approver scope: department first, then the division (DB-free)."""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

from types import SimpleNamespace as NS  # noqa: E402

import pytest  # noqa: E402

from routes.forms import form_approval_routes as fa  # noqa: E402

# departments 1, 2 → division 10; department 3 → division 20
DEPARTMENTS = {1: NS(division_id=10), 2: NS(division_id=10), 3: NS(division_id=20)}
# position_id → position level
POSITIONS = {101: 3, 105: 5}
RULE = NS(id=1, form_master_id=7, level_no=1, creator_min=1, creator_max=9,
          approve_by_type="position_level", approve_by_value=5)


def user(emp, dept, pos):
    return NS(employee_id=emp, department_id=dept, position_id=pos, employee_status="Active")


@pytest.fixture
def org(monkeypatch):
    def setup(users, pairs=()):
        cache = {
            "users_by_employee_id": {u.employee_id: u for u in users},
            "active_users": list(users),
            "positions_level_by_id": POSITIONS,
            "departments_by_id": DEPARTMENTS,
            "approver_division_pairs": set(pairs),
            "rules_by_form_level": {(7, 1): [RULE]},
            "eligible_approver_memo": {},
        }
        monkeypatch.setattr(fa, "_get_request_cache", lambda db: cache)
        return {u.employee_id: u for u in users}
    return setup


def can(u, approver):
    return fa.can_user_approve(db=None, rule=RULE, approver=approver,
                               approver_level=POSITIONS[approver.position_id], requester=u)


def test_department_approver_wins_over_division(org):
    u = org([user("req", 1, 101), user("dept_boss", 1, 105), user("div_boss", 2, 105)])
    assert can(u["req"], u["dept_boss"])
    assert not can(u["req"], u["div_boss"])


def test_falls_back_to_division_when_department_has_no_level(org):
    u = org([user("req", 1, 101), user("div_boss", 2, 105), user("other_div", 3, 105)])
    assert fa.get_applicable_rule_with_fallback(None, 7, 3, 1, u["req"]) is RULE
    assert can(u["req"], u["div_boss"])
    assert not can(u["req"], u["other_div"])


def test_responsibility_table_counts_as_department_scope(org):
    # "assigned" sits in division 20 but is mapped to division 10 (the requester's)
    u = org([user("req", 1, 101), user("assigned", 3, 105), user("div_boss", 2, 105)],
            pairs=[("assigned", 10)])
    assert can(u["req"], u["assigned"])
    assert not can(u["req"], u["div_boss"])


def test_division_approver_still_needs_the_level(org):
    u = org([user("req", 1, 101), user("div_staff", 2, 101)])
    assert fa.get_applicable_rule_with_fallback(None, 7, 3, 1, u["req"]) is None
    assert not can(u["req"], u["div_staff"])


def test_requester_without_department_gets_no_division_fallback(org):
    u = org([user("req", None, 101), user("div_boss", 2, 105)])
    assert not can(u["req"], u["div_boss"])


def test_mapping_to_another_division_does_not_count(org):
    u = org([user("req", 1, 101), user("assigned", 3, 105)], pairs=[("assigned", 20)])
    assert not can(u["req"], u["assigned"])
