import inspect
import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

from routes.forms import form_approval_routes as approval_routes
from routes.forms import form_submission_routes as submission_routes


def test_submit_has_advance_guards():
    src = inspect.getsource(submission_routes.submit_form)
    assert "_guard_advance_values" in src
    assert 'status_approve, current_level = "In Progress", 1' in src
    guard = inspect.getsource(submission_routes._guard_advance_values)
    assert "check_account_no" in guard
    assert "check_bank" in guard
    assert "approval_repo.describe" in guard


def test_update_runs_advance_guard():
    src = inspect.getsource(submission_routes.update_form_details)
    assert "_guard_advance_values" in src


def test_update_locks_advance():
    src = inspect.getsource(submission_routes.update_form_details)
    assert "แก้ไขคำขอเบิกไม่ได้หลังอนุมัติ/ไม่อนุมัติแล้ว" in src
    assert "เฉพาะผู้ขอเบิกเท่านั้นที่แก้ไขคำขอได้" in src


def test_approve_reject_branch_on_advance():
    for fn in (approval_routes.approve_submission, approval_routes.reject_submission):
        src = inspect.getsource(fn)
        assert "approval_repo.approval_decision" in src
        assert 'decision["allowed"]' in src
        assert "with_for_update=True" in src  # concurrent approvals of one ADV serialize on the row


def test_approve_advance_uses_step_transition():
    src = inspect.getsource(approval_routes.approve_submission)
    assert "approval_repo.record_approval(db, submission, approver.id, decision, remark)" in src
    reject = inspect.getsource(approval_routes.reject_submission)
    assert 'level_no = decision["step"]' in reject and 'submission.status_approve = "Rejected"' in reject


def test_generic_pending_skips_advance():
    src = inspect.getsource(approval_routes.get_pending_approvals)
    assert "is_advance_submission(sub)" in src


# ---- behavioural, DB-free tests for the shared ADV guard ----
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from services.finance import advance_logic as L


def _form():
    qs = [
        NS(id=1, question_name="adv_amount", question_type="number", sort_order=1),
        NS(id=2, question_name="adv_bank", question_type="select", sort_order=2),
        NS(id=3, question_name="adv_account_no", question_type="text", sort_order=3),
        NS(id=4, question_name="adv_use_date", question_type="date", sort_order=4),
    ]
    return NS(questions=qs)


def _v(qid, text=None, number=None, date=None):
    return NS(question_id=qid, value_text=text, value_number=number, value_date=date)


def _values(bank="KBANK", account="123-456 7890", amount=1500):
    from datetime import date, timedelta
    vals = [_v(1, number=amount), _v(4, date=date.today() + timedelta(days=30))]
    if bank is not None:
        vals.append(_v(2, text=bank))
    if account is not None:
        vals.append(_v(3, text=account))
    return vals


@pytest.fixture
def described(monkeypatch):
    calls = []
    monkeypatch.setattr(submission_routes.approval_repo, "describe",
                        lambda db, created_by, amount: calls.append((created_by, amount)))
    return calls


def test_guard_normalizes_account_and_describes(described):
    vals = _values()
    submission_routes._guard_advance_values(None, _form(), vals, "E1")
    assert next(v for v in vals if v.question_id == 3).value_text == "1234567890"
    assert described == [("E1", 1500)]


def test_guard_rejects_unknown_bank(described):
    with pytest.raises(HTTPException) as ei:
        submission_routes._guard_advance_values(None, _form(), _values(bank="NOPE"), "E1")
    assert ei.value.status_code == 400 and "กรุณาเลือกธนาคารจากรายการ" in ei.value.detail
    assert described == []


def test_guard_rejects_bad_account(described):
    with pytest.raises(HTTPException) as ei:
        submission_routes._guard_advance_values(None, _form(), _values(account="๑๒๓๔๕๖๗๘๙๐"), "E1")
    assert ei.value.status_code == 400 and "เลขที่บัญชีไม่ถูกต้อง" in ei.value.detail


def test_guard_rejects_missing_account(described):
    with pytest.raises(HTTPException) as ei:
        submission_routes._guard_advance_values(None, _form(), _values(account=None), "E1")
    assert ei.value.status_code == 400 and "เลขที่บัญชีไม่ถูกต้อง" in ei.value.detail


def test_check_bank_helper():
    assert L.check_bank("SCB") == "SCB"
    for bad in (None, "", "scb", "XXX"):
        with pytest.raises(L.AdvanceRuleError):
            L.check_bank(bad)
