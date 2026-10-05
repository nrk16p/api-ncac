"""v3 §5 two-step approval — repo wiring (DB-free: loaders are monkeypatched, queries faked)."""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

from datetime import datetime  # noqa: E402
from decimal import Decimal  # noqa: E402
from types import SimpleNamespace as NS  # noqa: E402

import pytest  # noqa: E402

from services.finance import approval_logic as A  # noqa: E402
from services.finance import approval_repo as R  # noqa: E402
from services.finance.advance_logic import AdvanceRuleError  # noqa: E402
from tests.finance.test_approval_logic import TIERS  # noqa: E402


def person(eid, uid, level, dept, active=True):
    return {"employee_id": eid, "id": uid, "level": level, "department_id": dept, "active": active}


PEOPLE = {p["employee_id"]: p for p in [
    person("R", 1, 3, 10),       # requester level 3, dept 10
    person("L4", 2, 4, 10),      # หัวหน้าถัดไป
    person("L5", 4, 5, 10),      # MGR same dept
    person("L5x", 5, 5, 20),     # MGR other dept, mapped to 10
    person("L8", 6, 8, 30),      # CL mapped to 10
    person("N9", 7, 9, 99),      # org-wide
]}
MAP = {"L5x": {10}, "L8": {10}}
CTX = R.ApprovalContext(tiers=TIERS, people=PEOPLE, mappings=MAP)


def sub(sid=11, level=1, amount=None, created_by="R"):
    return NS(id=sid, form_id=f"ADV-2610-{sid:03d}", created_by=created_by, current_approval_level=level,
              status_approve="In Progress", created_at=datetime(2026, 10, 5, 2, 0))


def log(lid, level_no, action, action_by, at=None):
    return {"id": lid, "level_no": level_no, "action": action, "action_by": action_by, "action_at": at,
            "remark": None}


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setattr(R, "load_context", lambda db: CTX)


# ---------------------------- rounds ----------------------------

class _LogQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *a):
        return self

    def order_by(self, *a):
        return self

    def all(self):
        return sorted(self.rows, key=lambda r: r.id)


class _LogDb:
    def __init__(self, rows):
        self.rows = rows
        self.queries = 0

    def query(self, *a):
        self.queries += 1
        return _LogQuery(self.rows)


def _row(lid, sid, level_no, action, action_by):
    return NS(id=lid, submission_id=sid, level_no=level_no, action=action, action_by=action_by,
              action_at=datetime(2026, 10, 5, 3, lid), remark=None)


def test_current_round_logs_after_latest_resubmitted():
    db = _LogDb([_row(1, 11, 1, "APPROVED", 2), _row(2, 11, 2, "APPROVED", 4), _row(3, 11, 0, "RETURNED", 9),
                 _row(4, 11, 0, "RESUBMITTED", 1), _row(5, 11, 1, "APPROVED", 2)])
    got = R.current_round_logs(db, 11)
    assert [(l["id"], l["action"], l["level_no"], l["action_by"]) for l in got] == [(5, "APPROVED", 1, 2)]


def test_current_round_logs_without_marker_is_everything():
    db = _LogDb([_row(2, 11, 1, "APPROVED", 2), _row(1, 11, 0, "RETURNED", 9)])
    assert [l["id"] for l in R.current_round_logs(db, 11)] == [1, 2]
    assert R.current_round_logs(_LogDb([]), 11) == []


def test_round_logs_grouped_per_submission_in_one_query():
    db = _LogDb([_row(1, 11, 1, "APPROVED", 2), _row(2, 12, 0, "RESUBMITTED", 1), _row(3, 12, 1, "APPROVED", 5)])
    got = R.round_logs_by_submission(db, [11, 12])
    assert db.queries == 1
    assert [l["id"] for l in got[11]] == [1] and [l["id"] for l in got[12]] == [3]
    assert R.round_logs_by_submission(_LogDb([]), []) == {}


# ---------------------------- decision + transitions ----------------------------

def _decide(monkeypatch, approver, level=1, amount="15000", logs=()):
    monkeypatch.setattr(R, "_amount_of", lambda db, sid: Decimal(amount))
    monkeypatch.setattr(R, "current_round_logs", lambda db, sid: list(logs))
    return R.approval_decision(None, sub(level=level), approver)


def test_decision_uses_current_step_and_excludes_step_1_approver(ctx, monkeypatch):
    assert _decide(monkeypatch, "L4")["allowed"] is True
    logs = [log(1, 1, "APPROVED", PEOPLE["L4"]["id"])]
    assert _decide(monkeypatch, "L4", level=2, logs=logs)["allowed"] is False   # below level 5 and step-1 approver
    logs = [log(1, 1, "APPROVED", PEOPLE["L8"]["id"])]
    assert _decide(monkeypatch, "L8", level=2, amount="60000", logs=logs)["allowed"] is False
    assert _decide(monkeypatch, "N9", level=2, amount="60000", logs=logs)["allowed"] is True


def test_decision_bad_amount_denies(ctx, monkeypatch):
    monkeypatch.setattr(R, "_amount_of", lambda db, sid: None)
    assert R.approval_decision(None, sub(), "L4") == {"allowed": False}
    assert R.can_approve_submission(None, sub(), "L4") is False


class _AddDb:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)


def _logs(db):
    return [(l.level_no, l.action, l.action_by, l.remark) for l in db.added]


def test_record_step_1_moves_to_step_2(ctx, monkeypatch):
    s = sub()
    decision = _decide(monkeypatch, "L4")
    db = _AddDb()
    R.record_approval(db, s, 2, decision, "ok")
    assert _logs(db) == [(1, "APPROVED", 2, "ok")]
    assert (s.status_approve, s.current_approval_level) == ("In Progress", 2)


def test_record_dynamic_skip_logs_step_2_and_approves(ctx, monkeypatch):
    s = sub()
    decision = _decide(monkeypatch, "L5")
    db = _AddDb()
    R.record_approval(db, s, 4, decision, None)
    assert [(lv, act, by) for lv, act, by, _ in _logs(db)] == [(1, "APPROVED", 4), (2, "APPROVED", 4)]
    assert db.added[1].remark == A.skip_remark(2, 1, 5, 5)
    assert (s.status_approve, s.current_approval_level) == ("Approved", 1)


def test_record_step_2_approves(ctx, monkeypatch):
    s = sub(level=2)
    decision = _decide(monkeypatch, "L5", level=2, logs=[log(1, 1, "APPROVED", 2)])
    db = _AddDb()
    R.record_approval(db, s, 4, decision, "TOA")
    assert _logs(db) == [(2, "APPROVED", 4, "TOA")]
    assert (s.status_approve, s.current_approval_level) == ("Approved", 2)


def test_record_single_step_approves(ctx, monkeypatch):
    s = sub()
    decision = _decide(monkeypatch, "L4", amount="1500")
    db = _AddDb()
    R.record_approval(db, s, 2, decision)
    assert _logs(db) == [(1, "APPROVED", 2, None)] and s.status_approve == "Approved"


# ---------------------------- pending queue ----------------------------

@pytest.fixture
def queue(ctx, monkeypatch):
    subs = [sub(21, level=1), sub(22, level=2), sub(23, level=1)]
    amounts = {21: Decimal("15000"), 22: Decimal("15000"), 23: Decimal("1500")}
    rounds = {22: [log(1, 1, "APPROVED", PEOPLE["L4"]["id"])]}
    monkeypatch.setattr(R, "_in_progress_advances", lambda db: subs)
    monkeypatch.setattr(R.advance_repo, "request_values_by_submission",
                        lambda db, ids: {i: {"amount": amounts[i]} for i in ids})
    monkeypatch.setattr(R, "round_logs_by_submission", lambda db, ids: {i: rounds.get(i, []) for i in ids})
    monkeypatch.setattr(R.advance_repo, "people_by_employee_id", lambda db, ids: {})


def _queue(employee_id):
    return {i["submission_id"]: (i["step"], i["total_steps"], i["tab"]) for i in R.pending_for(None, employee_id)}


def test_pending_leader_sees_step_1_only(queue):
    assert _queue("L4") == {21: (1, 2, "mine"), 23: (1, 1, "mine")}


def test_pending_toa_sees_step_2_as_mine_and_step_1_as_delegable(queue):
    assert _queue("L5") == {21: (1, 2, "delegable"), 22: (2, 2, "mine"), 23: (1, 1, "delegable")}


def test_pending_item_shape(queue):
    item = next(i for i in R.pending_for(None, "L5") if i["submission_id"] == 22)
    assert item["tier"] == {"clause": "6.5", "approver_label": "MGR / SM / DPCL (ระดับ 5–7)", "required_level": 5}
    assert (item["step"], item["total_steps"]) == (2, 2)
    assert item["step_required_level"] == 5
    assert item["step_label"] == "ระดับ 5 ขึ้นไป (ข้อ 6.5 · MGR / SM / DPCL (ระดับ 5–7))"
    assert item["form_id"] == "ADV-2610-022" and item["request"]["amount"] == 15000.0


def test_pending_unknown_or_levelless_user_empty(queue):
    assert R.pending_for(None, "NOPE") == []


# ---------------------------- suggested approvers + detail ----------------------------

def test_suggested_approvers_are_for_current_step(ctx, monkeypatch):
    monkeypatch.setattr(R, "_amount_of", lambda db, sid: Decimal("60000"))
    monkeypatch.setattr(R.advance_repo, "people_by_employee_id",
                        lambda db, ids: {e: {"name": e, "position": None, "department": None} for e in ids})
    monkeypatch.setattr(R, "current_round_logs", lambda db, sid: [])
    s1 = R.suggested_approvers(None, sub(level=1))
    assert [a["employee_id"] for a in s1["approvers"]] == ["L4"]
    assert (s1["step"], s1["total_steps"], s1["step_required_level"], s1["required_level"]) == (1, 2, 4, 8)
    monkeypatch.setattr(R, "current_round_logs", lambda db, sid: [log(1, 1, "APPROVED", PEOPLE["L4"]["id"])])
    s2 = R.suggested_approvers(None, sub(level=2))
    assert [a["employee_id"] for a in s2["approvers"]] == ["L8"]
    assert s2["step_label"] == "ระดับ 8 ขึ้นไป (ข้อ 6.4 · CL (ระดับ 8))"


def test_detail_approval_contract(ctx, monkeypatch):
    at = datetime(2026, 10, 5, 3, 0)
    monkeypatch.setattr(R, "current_round_logs", lambda db, sid: [log(9, 1, "APPROVED", 2, at)])
    monkeypatch.setattr(R, "_users_by_id",
                        lambda db, ids: {2: NS(id=2, employee_id="L4", firstname="สมชาย", lastname="ใจดี")})
    got = R.detail_approval(None, sub(level=2), 15000.0)
    assert got == {
        "clause": "6.5", "approver_label": "MGR / SM / DPCL (ระดับ 5–7)", "required_level": 5,
        "steps": [{"step": 1, "required_level": 4, "label": "หัวหน้าระดับ 4 ขึ้นไป"},
                  {"step": 2, "required_level": 5, "label": "ระดับ 5 ขึ้นไป (ข้อ 6.5 · MGR / SM / DPCL (ระดับ 5–7))"}],
        "current_step": 2,
        "step_approvals": [{"step": 1, "employee_id": "L4", "name": "สมชาย ใจดี",
                            "action_at": "2026-10-05T03:00:00+00:00"}],
    }


def test_detail_approval_raises_rule_error_for_bad_amount(ctx, monkeypatch):
    monkeypatch.setattr(R, "current_round_logs", lambda db, sid: [])
    with pytest.raises(AdvanceRuleError):
        R.detail_approval(None, sub(), None)
