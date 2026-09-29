import inspect
import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

from routes.forms import form_approval_routes as approval_routes
from routes.forms import form_submission_routes as submission_routes


def test_submit_has_advance_guards():
    src = inspect.getsource(submission_routes.submit_form)
    assert "check_account_no" in src
    assert "approval_repo.describe" in src
    assert 'status_approve, current_level = "In Progress", 1' in src


def test_update_locks_advance():
    src = inspect.getsource(submission_routes.update_form_details)
    assert "แก้ไขคำขอเบิกไม่ได้หลังอนุมัติ/ไม่อนุมัติแล้ว" in src
    assert "เฉพาะผู้ขอเบิกเท่านั้นที่แก้ไขคำขอได้" in src


def test_approve_reject_branch_on_advance():
    for fn in (approval_routes.approve_submission, approval_routes.reject_submission):
        src = inspect.getsource(fn)
        assert "approval_repo.can_approve_submission" in src


def test_generic_pending_skips_advance():
    src = inspect.getsource(approval_routes.get_pending_approvals)
    assert "is_advance_submission(sub)" in src
