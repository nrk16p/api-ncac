"""v3 §6 ตีกลับให้ผู้เบิกแก้ไข: RETURNED status, return / resubmit routes, approval rounds, the ADV edit lock,
migration v3 parity and request.payee_type (DB-free)."""
import os
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

import pytest  # noqa: E402
from fastapi import BackgroundTasks, HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.schema import CreateTable  # noqa: E402

from models.finance_model import FinAdvance, FinAdvanceLog, FinPayeeAccount  # noqa: E402
from models.master_model import FormApprovalLog, FormSubmission, FormSubmissionValue  # noqa: E402
from models.user_model import User  # noqa: E402
from routes.finance import advance_routes as ar  # noqa: E402
from routes.forms import form_submission_routes as sr  # noqa: E402
from schemas.finance_schema import ResubmitIn, ReturnIn  # noqa: E402
from services.finance import advance_logic as L  # noqa: E402
from services.finance import approval_logic as rules  # noqa: E402
from services.finance import approval_repo  # noqa: E402

TODAY = date(2026, 10, 5)
ALL_STATUSES = (L.PENDING_APPROVAL, L.REJECTED, L.AWAITING_VOUCHER, L.AWAITING_PAYMENT, L.AWAITING_CLEARING,
                L.SENT_BACK, L.AWAITING_REVIEW, L.CLOSED, L.RETURNED)
FIN_VALUES = (L.FIN_VOUCHERED, L.FIN_VOUCHER_REJECTED, L.FIN_RETURNED, L.FIN_RESUBMITTED, L.FIN_PAID,
              L.FIN_CLEARING_SUBMITTED, L.FIN_SENT_BACK, L.FIN_CLOSED)
PROD_FIN_STATUSES = ("VOUCHERED", "VOUCHER_REJECTED", "PAID", "CLEARING_SUBMITTED", "SENT_BACK", "CLOSED")
V3_FIN_STATUSES = ["VOUCHERED", "VOUCHER_REJECTED", "RETURNED", "RESUBMITTED", "PAID", "CLEARING_SUBMITTED",
                   "SENT_BACK", "CLOSED"]
MIGRATION = Path(__file__).resolve().parents[2] / "scripts/migrations/2026-10-05_finance_advance_v3.sql"


# ---------------------------- derive_status (spec §6 precedence) ----------------------------

_APPROVED_MAP = {
    None: L.AWAITING_VOUCHER,
    L.FIN_VOUCHER_REJECTED: L.AWAITING_VOUCHER,
    L.FIN_RESUBMITTED: L.AWAITING_VOUCHER,
    L.FIN_RETURNED: L.RETURNED,
    L.FIN_VOUCHERED: L.AWAITING_PAYMENT,
    L.FIN_PAID: L.AWAITING_CLEARING,
    L.FIN_SENT_BACK: L.SENT_BACK,
    L.FIN_CLEARING_SUBMITTED: L.AWAITING_REVIEW,
    L.FIN_CLOSED: L.CLOSED,
}


@pytest.mark.parametrize("fin_status", (None,) + FIN_VALUES)
@pytest.mark.parametrize("status_approve", ("In Progress", "Approved", "Rejected"))
def test_derive_status_every_combination(status_approve, fin_status):
    expected = {"In Progress": L.PENDING_APPROVAL, "Rejected": L.REJECTED}.get(status_approve)
    if expected is None:
        expected = _APPROVED_MAP[fin_status]
    assert L.derive_status(status_approve, fin_status, None, TODAY) == (expected, False)


def test_approved_map_covers_every_fin_status():
    assert set(_APPROVED_MAP) == {None, *FIN_VALUES}
    assert sorted(FIN_VALUES) == sorted(V3_FIN_STATUSES)


@pytest.mark.parametrize("fin_status", (L.FIN_PAID, L.FIN_SENT_BACK))
def test_overdue_only_when_approved(fin_status):
    past = TODAY - timedelta(days=1)
    assert L.derive_status("Approved", fin_status, past, TODAY)[1] is True
    # the approval state wins over the fin row: never overdue outside Approved
    assert L.derive_status("In Progress", fin_status, past, TODAY) == (L.PENDING_APPROVAL, False)
    assert L.derive_status("Rejected", fin_status, past, TODAY) == (L.REJECTED, False)


def test_returned_never_overdue():
    assert L.derive_status("Approved", L.FIN_RETURNED, TODAY - timedelta(days=30), TODAY) == (L.RETURNED, False)


def test_returned_status_and_label():
    assert L.RETURNED == "RETURNED" and L.FIN_RETURNED == "RETURNED" and L.FIN_RESUBMITTED == "RESUBMITTED"
    assert L.STATUS_LABELS[L.RETURNED] == "ตีกลับให้ผู้เบิกแก้ไข"
    for status in ALL_STATUSES:
        assert L.STATUS_LABELS[status]


# ---------------------------- check_return / check_resubmit ----------------------------

@pytest.mark.parametrize("status", (L.AWAITING_VOUCHER, L.AWAITING_PAYMENT))
def test_check_return_allowed(status):
    L.check_return(status, remark="บัญชีผิด")


@pytest.mark.parametrize("status", [s for s in ALL_STATUSES if s not in (L.AWAITING_VOUCHER, L.AWAITING_PAYMENT)])
def test_check_return_409_elsewhere(status):
    with pytest.raises(L.InvalidTransition) as ei:
        L.check_return(status, remark="x")
    assert ei.value.http_status == 409 and "ตีกลับให้ผู้เบิกแก้ไข" in str(ei.value)


@pytest.mark.parametrize("remark", [None, "", "   "])
def test_check_return_requires_remark(remark):
    with pytest.raises(L.AdvanceRuleError) as ei:
        L.check_return(L.AWAITING_PAYMENT, remark=remark)
    assert ei.value.http_status == 400 and str(ei.value) == "กรุณาระบุเหตุผลที่ตีกลับ"


def test_check_resubmit_owner_at_returned():
    L.check_resubmit(L.RETURNED, is_owner=True)


@pytest.mark.parametrize("status", ALL_STATUSES)
def test_check_resubmit_not_owner_is_403(status):
    with pytest.raises(L.NotAllowed) as ei:
        L.check_resubmit(status, is_owner=False)
    assert ei.value.http_status == 403


@pytest.mark.parametrize("status", [s for s in ALL_STATUSES if s != L.RETURNED])
def test_check_resubmit_409_elsewhere(status):
    with pytest.raises(L.InvalidTransition) as ei:
        L.check_resubmit(status, is_owner=True)
    assert ei.value.http_status == 409


# ---------------------------- edit lock (pure) ----------------------------

def test_requester_edit_allowed_cases():
    L.check_requester_edit(L.PENDING_APPROVAL, is_owner=True, step1_approved=False)
    L.check_requester_edit(L.RETURNED, is_owner=True, step1_approved=False)
    L.check_requester_edit(L.RETURNED, is_owner=True, step1_approved=True)  # round-1 approvals don't matter


def test_requester_edit_locked_after_step1():
    with pytest.raises(L.InvalidTransition) as ei:
        L.check_requester_edit(L.PENDING_APPROVAL, is_owner=True, step1_approved=True)
    assert ei.value.http_status == 409 and str(ei.value) == L.EDIT_LOCKED_STEP1


@pytest.mark.parametrize("status", [s for s in ALL_STATUSES if s not in (L.PENDING_APPROVAL, L.RETURNED)])
def test_requester_edit_locked_elsewhere(status):
    with pytest.raises(L.InvalidTransition) as ei:
        L.check_requester_edit(status, is_owner=True, step1_approved=False)
    assert ei.value.http_status == 409 and str(ei.value) == L.EDIT_LOCKED


@pytest.mark.parametrize("status", ALL_STATUSES)
def test_requester_edit_not_owner_is_403(status):
    with pytest.raises(L.NotAllowed) as ei:
        L.check_requester_edit(status, is_owner=False, step1_approved=False)
    assert ei.value.http_status == 403 and str(ei.value) == "เฉพาะผู้ขอเบิกเท่านั้นที่แก้ไขคำขอได้"


# ---------------------------- approval rounds ----------------------------

def _log(id_, level_no, action, action_by):
    return {"id": id_, "level_no": level_no, "action": action, "action_by": action_by, "action_at": None,
            "remark": None}


ROUND_1 = [_log(1, 1, "APPROVED", 101), _log(2, 2, "APPROVED", 102), _log(3, 0, "RETURNED", 900)]


def test_leader_approved():
    assert rules.leader_approved([]) is False
    assert rules.leader_approved([_log(1, 1, "APPROVED", 101)]) is True
    assert rules.leader_approved([_log(1, 1, "REJECTED", 101)]) is False
    assert rules.leader_approved([_log(1, 0, "RETURNED", 900), _log(2, 0, "RESUBMITTED", 7)]) is False
    assert rules.leader_approved([], current_level=2) is True
    assert rules.leader_approved([], current_level=1) is False
    assert rules.leader_approved([], current_level=None) is False


def test_round_one_never_counts_after_resubmit():
    logs = ROUND_1 + [_log(4, 0, "RESUBMITTED", 7)]
    current = rules.current_round(logs)
    assert current == []
    assert rules.step_approvals(current) == []
    assert rules.leader_approved(current, current_level=1) is False
    assert rules.excluded_approver(current, rules.STEP_TOA) == frozenset()
    # round 2: the round-2 step-1 approver (102 approved step 2 in round 1) is now excluded at step 2
    logs += [_log(5, 1, "APPROVED", 102)]
    current = rules.current_round(logs)
    assert [a["action_by"] for a in rules.step_approvals(current)] == [102]
    assert rules.excluded_approver(current, rules.STEP_TOA) == frozenset({102})
    assert rules.leader_approved(current) is True


def test_round_logs_from_db_rows():
    rows = [NS(submission_id=5, **log) for log in ROUND_1 + [_log(4, 0, "RESUBMITTED", 7), _log(5, 1, "APPROVED", 103)]]

    class Q:
        def filter(self, *a):
            return self

        def order_by(self, *a):
            return self

        def all(self):
            return rows

    db = NS(query=lambda *a: Q())
    assert [log["id"] for log in approval_repo.current_round_logs(db, 5)] == [5]


# ---------------------------- payee_type in the request ----------------------------

def _row(name, type_, sort, text):
    return {"name": name, "type": type_, "sort_order": sort, "text": text, "number": None, "date": None}


def test_pick_request_values_payee_type():
    rows = [_row("adv_purpose", "longtext", 1, "p"), _row("adv_payee_type", "dropdown", 5, "SUPPLIER")]
    assert L.pick_request_values(rows)["payee_type"] == "SUPPLIER"
    # by name only: an unnamed dropdown is not taken for payee_type
    assert L.pick_request_values([_row("x", "dropdown", 5, "SELF")])["payee_type"] is None
    assert L.pick_request_values([])["payee_type"] is None


@pytest.mark.parametrize("raw,expected", [("SELF", "SELF"), ("SUPPLIER", "SUPPLIER"), (None, None),
                                          ("", None), ("OTHER", None)])
def test_serialize_request_payee_type(raw, expected):
    from services.finance.advance_repo import serialize_request
    assert serialize_request({"payee_type": raw})["payee_type"] == expected


# ---------------------------- migration v3 + model parity ----------------------------

def _check_list(text):
    match = re.search(r"fin_status IN \(([^)]*)\)", text)
    return [v.strip().strip("'") for v in match.group(1).replace('"', "").split(",") if v.strip()]


def test_model_check_matches_migration():
    ddl = str(CreateTable(FinAdvance.__table__).compile(dialect=postgresql.dialect()))
    sql = MIGRATION.read_text(encoding="utf-8")
    assert _check_list(ddl) == V3_FIN_STATUSES
    assert _check_list(sql) == V3_FIN_STATUSES
    assert set(PROD_FIN_STATUSES) <= set(V3_FIN_STATUSES)  # existing prod rows keep passing


def test_migration_is_one_idempotent_transaction():
    sql = MIGRATION.read_text(encoding="utf-8")
    body = sql[sql.index("BEGIN;"):sql.index("COMMIT;")]
    assert "ALTER TABLE fin_advances DROP CONSTRAINT IF EXISTS ck_fin_advances_fin_status;" in body
    assert "ALTER TABLE fin_advances ADD CONSTRAINT ck_fin_advances_fin_status" in body
    assert body.index("DROP CONSTRAINT") < body.index("ADD CONSTRAINT")
    tail = sql[sql.index("COMMIT;"):]
    assert "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_fin_advances_fin_status'" in tail
    assert "DO $$" not in sql and sql.count("BEGIN;") == 1 and sql.count("COMMIT;") == 1


def test_fin_log_and_marker_columns_fit():
    assert len("RESUBMIT") <= FinAdvanceLog.__table__.c.action.type.length
    assert len(rules.MARKER_RESUBMITTED) <= FormApprovalLog.__table__.c.action.type.length
    assert len(L.FIN_RESUBMITTED) <= FinAdvance.__table__.c.fin_status.type.length
    # the pay/voucher columns a RETURNED row is created without are nullable
    for column in ("acc_code", "voucher_no", "voucher_date", "purpose", "amount_paid", "transfer_date",
                   "clear_due_date"):
        assert FinAdvance.__table__.c[column].nullable, column


# ---------------------------- schemas ----------------------------

def test_return_in_schema():
    assert ReturnIn(action_by="F1", remark="  ").remark is None  # blank → check_return's Thai 400
    assert ReturnIn(action_by="F1").remark is None
    assert ReturnIn(action_by="F1", remark=" ผิดบัญชี ").remark == "ผิดบัญชี"
    with pytest.raises(ValidationError):
        ReturnIn(remark="x")
    with pytest.raises(ValidationError):
        ReturnIn(action_by="F1", remark="x" * 1001)


def test_resubmit_in_schema():
    assert ResubmitIn(action_by="E1").action_by == "E1"
    with pytest.raises(ValidationError):
        ResubmitIn()
    with pytest.raises(ValidationError):
        ResubmitIn(action_by="  ")


# ---------------------------- return / resubmit routes ----------------------------

class _Q:
    def __init__(self, result):
        self.result = result

    def filter(self, *a):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self.result


class _Db:
    def __init__(self, adv=None, requester=None, flush_error=False, master=None):
        self.adv, self.requester, self.flush_error, self.master = adv, requester, flush_error, master
        self.added, self.committed, self.rolled_back = [], False, False

    def query(self, entity, *a):
        if entity is FinAdvance:
            return _Q(self.adv)
        if entity is User:
            return _Q(self.requester)
        if entity is FinPayeeAccount:  # the resubmit guard's SELF master lookup
            return _Q(self.master)
        raise AssertionError(f"unexpected query {entity}")

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        if self.flush_error:
            raise IntegrityError("x", {}, Exception("dup"))
        for obj in self.added:
            if isinstance(obj, FinAdvance) and obj.id is None:
                obj.id = 42

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def of(self, kind):
        return [o for o in self.added if isinstance(o, kind)]


FINANCE = NS(id=77, employee_id="F1", department_id=4)
REQUESTER = NS(id=55, employee_id="E1")


_ADV_QUESTIONS = ["adv_amount", "adv_payee_type", "adv_bank", "adv_account_no", "adv_account_name", "adv_use_date"]
_ADV_TYPES = ["number", "dropdown", "select", "text", "text", "date"]


def _stored(qid, text=None, number=None, d=None):
    return NS(question_id=qid, value_text=text, value_number=number, value_date=d, value_boolean=None)


def _supplier_values(use_date=TODAY + timedelta(days=5)):
    """Stored ADV values (questions: 1 amount, 2 payee type, 3 bank, 4 account no, 5 account name, 6 use date)."""
    return [_stored(1, number=Decimal("15000")), _stored(2, "SUPPLIER"), _stored(3, "SCB"),
            _stored(4, "1234567890"), _stored(5, "Supplier Co"), _stored(6, d=use_date)]


def _sub(status_approve="Approved", level=2, values=None):
    form = NS(questions=[NS(id=i + 1, question_name=n, question_type=t, sort_order=i + 1)
                         for i, (n, t) in enumerate(zip(_ADV_QUESTIONS, _ADV_TYPES))])
    return NS(id=5, form_id="ADV-2610-001", status_approve=status_approve, current_approval_level=level,
              created_by="E1", form=form, values=_supplier_values() if values is None else values)


def _adv(fin_status, **kw):
    return FinAdvance(id=10, submission_id=5, form_id="ADV-2610-001", fin_status=fin_status, **kw)


@pytest.fixture
def wired(monkeypatch):
    state = {"sub": _sub(), "describe": []}
    monkeypatch.setattr(ar.repo, "get_advance_submission", lambda db, form_id: state["sub"])
    monkeypatch.setattr(ar.repo, "get_advance_detail", lambda db, form_id: {"form_id": form_id, "detail": True})
    monkeypatch.setattr(ar.repo, "today_bkk", lambda: TODAY)
    monkeypatch.setattr(ar, "require_finance", lambda db, employee_id: FINANCE)
    monkeypatch.setattr(ar.repo, "request_values_by_submission",
                        lambda db, ids: {5: {"amount": Decimal("15000")}})
    monkeypatch.setattr(ar.approval_repo, "describe",
                        lambda db, eid, amount: state["describe"].append((eid, amount)))
    monkeypatch.setattr(ar.advance_guard, "today_bkk", lambda: TODAY)
    state["locked"] = []

    def lock_submission_with_values(db, submission_id):
        state["locked"].append(submission_id)
        return state["sub"]

    monkeypatch.setattr(ar.repo, "lock_submission_with_values", lock_submission_with_values)
    return state


def test_routes_registered():
    routes = {(r.path, tuple(sorted(r.methods))) for r in ar.router.routes}
    assert ("/finance/advances/{form_id}/return", ("PUT",)) in routes
    assert ("/finance/advances/{form_id}/resubmit", ("PUT",)) in routes


def test_return_without_fin_row_creates_returned_row(wired):
    db = _Db(adv=None)
    out = ar.return_advance("ADV-2610-001", ReturnIn(action_by="F1", remark=" บัญชีผิด "), BackgroundTasks(), db)
    assert out == {"form_id": "ADV-2610-001", "detail": True} and db.committed
    (adv,) = db.of(FinAdvance)
    assert (adv.submission_id, adv.form_id, adv.fin_status) == (5, "ADV-2610-001", "RETURNED")
    assert adv.amount_paid is None and adv.voucher_no is None and adv.purpose is None
    (log,) = db.of(FinAdvanceLog)
    assert (log.advance_id, log.action, log.remark, log.action_by) == (42, "RETURN", "บัญชีผิด", "F1")
    assert log.changes == {"fin_status": [None, "RETURNED"]}
    (marker,) = db.of(FormApprovalLog)
    assert (marker.submission_id, marker.level_no, marker.action, marker.action_by, marker.remark) == \
        (5, 0, "RETURNED", 77, "บัญชีผิด")


def test_return_at_awaiting_payment_clears_voucher(wired):
    adv = _adv("VOUCHERED", voucher_no="SADV-1", voucher_date=date(2026, 10, 1))
    db = _Db(adv=adv)
    ar.return_advance("ADV-2610-001", ReturnIn(action_by="F1", remark="ผิด"), BackgroundTasks(), db)
    assert (adv.fin_status, adv.voucher_no, adv.voucher_date) == ("RETURNED", None, None)
    assert db.of(FinAdvance) == []  # the existing row is reused
    (log,) = db.of(FinAdvanceLog)
    assert log.advance_id == 10 and log.changes == {
        "fin_status": ["VOUCHERED", "RETURNED"], "voucher_no": ["SADV-1", None], "voucher_date": ["2026-10-01", None]}
    assert db.of(FormApprovalLog)[0].action == "RETURNED"


def test_return_after_legacy_voucher_reject(wired):
    adv = _adv("VOUCHER_REJECTED", voucher_no="SADV-1", voucher_date=date(2026, 10, 1))
    db = _Db(adv=adv)
    ar.return_advance("ADV-2610-001", ReturnIn(action_by="F1", remark="ผิด"), BackgroundTasks(), db)
    assert (adv.fin_status, adv.voucher_no) == ("RETURNED", None)


@pytest.mark.parametrize("fin_status", ["PAID", "SENT_BACK", "CLEARING_SUBMITTED", "CLOSED", "RETURNED"])
def test_return_409_after_payment_or_when_already_returned(wired, fin_status):
    db = _Db(adv=_adv(fin_status))
    with pytest.raises(HTTPException) as ei:
        ar.return_advance("ADV-2610-001", ReturnIn(action_by="F1", remark="x"), BackgroundTasks(), db)
    assert ei.value.status_code == 409 and db.added == [] and not db.committed


@pytest.mark.parametrize("status_approve", ["In Progress", "Rejected"])
def test_return_409_before_approval(wired, status_approve):
    wired["sub"] = _sub(status_approve)
    db = _Db(adv=None)
    with pytest.raises(HTTPException) as ei:
        ar.return_advance("ADV-2610-001", ReturnIn(action_by="F1", remark="x"), BackgroundTasks(), db)
    assert ei.value.status_code == 409 and db.added == []


def test_return_requires_remark(wired):
    db = _Db(adv=None)
    with pytest.raises(HTTPException) as ei:
        ar.return_advance("ADV-2610-001", ReturnIn(action_by="F1", remark="  "), BackgroundTasks(), db)
    assert (ei.value.status_code, ei.value.detail) == (400, "กรุณาระบุเหตุผลที่ตีกลับ") and db.added == []


def test_return_requires_finance(wired, monkeypatch):
    def deny(db, employee_id):
        raise HTTPException(status_code=403, detail="เฉพาะฝ่ายการเงินเท่านั้น")

    monkeypatch.setattr(ar, "require_finance", deny)
    db = _Db(adv=None)
    with pytest.raises(HTTPException) as ei:
        ar.return_advance("ADV-2610-001", ReturnIn(action_by="E1", remark="x"), BackgroundTasks(), db)
    assert ei.value.status_code == 403 and db.added == []


def test_return_concurrent_insert_is_409(wired):
    db = _Db(adv=None, flush_error=True)
    with pytest.raises(HTTPException) as ei:
        ar.return_advance("ADV-2610-001", ReturnIn(action_by="F1", remark="x"), BackgroundTasks(), db)
    assert ei.value.status_code == 409 and db.rolled_back and not db.committed


def test_resubmit_restarts_approval(wired):
    adv = _adv("RETURNED")
    db = _Db(adv=adv, requester=REQUESTER)
    out = ar.resubmit_advance("ADV-2610-001", ResubmitIn(action_by="E1"), BackgroundTasks(), db)
    sub = wired["sub"]
    assert out["detail"] and db.committed
    assert (adv.fin_status, sub.status_approve, sub.current_approval_level) == ("RESUBMITTED", "In Progress", 1)
    assert wired["describe"] == [("E1", Decimal("15000"))] and wired["locked"] == [5]
    (log,) = db.of(FinAdvanceLog)
    assert (log.advance_id, log.action, log.action_by) == (10, "RESUBMIT", "E1")
    assert log.changes == {"fin_status": ["RETURNED", "RESUBMITTED"], "status_approve": ["Approved", "In Progress"],
                           "current_approval_level": [2, 1]}
    (marker,) = db.of(FormApprovalLog)
    assert (marker.submission_id, marker.level_no, marker.action, marker.action_by) == (5, 0, "RESUBMITTED", 55)
    # after the resubmit the request is pending approval again (step 1)
    assert L.derive_status(sub.status_approve, adv.fin_status, None, TODAY) == (L.PENDING_APPROVAL, False)


def test_resubmit_not_owner_is_403(wired):
    db = _Db(adv=_adv("RETURNED"), requester=REQUESTER)
    with pytest.raises(HTTPException) as ei:
        ar.resubmit_advance("ADV-2610-001", ResubmitIn(action_by="F1"), BackgroundTasks(), db)
    assert ei.value.status_code == 403 and db.added == [] and wired["sub"].status_approve == "Approved"


@pytest.mark.parametrize("fin_status", [None, "VOUCHERED", "RESUBMITTED", "PAID"])
def test_resubmit_409_unless_returned(wired, fin_status):
    db = _Db(adv=_adv(fin_status) if fin_status else None, requester=REQUESTER)
    with pytest.raises(HTTPException) as ei:
        ar.resubmit_advance("ADV-2610-001", ResubmitIn(action_by="E1"), BackgroundTasks(), db)
    assert ei.value.status_code == 409 and db.added == []


def test_resubmit_409_while_in_approval(wired):
    wired["sub"] = _sub("In Progress", 1)
    db = _Db(adv=_adv("RESUBMITTED"), requester=REQUESTER)
    with pytest.raises(HTTPException) as ei:
        ar.resubmit_advance("ADV-2610-001", ResubmitIn(action_by="E1"), BackgroundTasks(), db)
    assert ei.value.status_code == 409


def test_resubmit_without_eligible_approver_is_400(wired, monkeypatch):
    def none_eligible(db, eid, amount):
        raise L.AdvanceRuleError(rules.MSG_NO_APPROVER)

    monkeypatch.setattr(ar.approval_repo, "describe", none_eligible)
    adv = _adv("RETURNED")
    db = _Db(adv=adv, requester=REQUESTER)
    with pytest.raises(HTTPException) as ei:
        ar.resubmit_advance("ADV-2610-001", ResubmitIn(action_by="E1"), BackgroundTasks(), db)
    assert (ei.value.status_code, ei.value.detail) == (400, rules.MSG_NO_APPROVER)
    assert adv.fin_status == "RETURNED" and db.added == []


# ---- resubmit re-runs the full value guard (fix batch #1) ----

@pytest.mark.parametrize("use_date,ok", [(TODAY - timedelta(days=1), False), (TODAY - timedelta(days=40), False),
                                         (TODAY, True)])
def test_resubmit_checks_stored_use_date(wired, use_date, ok):
    wired["sub"] = _sub(values=_supplier_values(use_date=use_date))
    adv = _adv("RETURNED")
    db = _Db(adv=adv, requester=REQUESTER)
    if ok:
        ar.resubmit_advance("ADV-2610-001", ResubmitIn(action_by="E1"), BackgroundTasks(), db)
        assert db.committed and adv.fin_status == "RESUBMITTED"
        return
    with pytest.raises(HTTPException) as ei:
        ar.resubmit_advance("ADV-2610-001", ResubmitIn(action_by="E1"), BackgroundTasks(), db)
    assert (ei.value.status_code, ei.value.detail) == (400, L.USE_DATE_MESSAGE)
    assert (adv.fin_status, wired["sub"].status_approve, wired["sub"].current_approval_level) == ("RETURNED",
                                                                                                  "Approved", 2)
    assert L.derive_status(wired["sub"].status_approve, adv.fin_status, None, TODAY)[0] == L.RETURNED
    assert db.added == [] and not db.committed and wired["describe"] == []


def _self_values(account_no="1112223334", account_name="สมชาย เก่า", extra=()):
    return [_stored(1, number=Decimal("15000")), _stored(2, "SELF"), _stored(3, "KBANK"),
            _stored(4, account_no), _stored(5, account_name), _stored(6, d=TODAY + timedelta(days=5)), *extra]


def test_resubmit_persists_the_current_self_master(wired):
    """Finance returned the request (wrong account) and approved the requester's new master since: the resubmit
    re-snapshots the master into every stored row, so the old account is never paid."""
    stored = _self_values(extra=(_stored(4, "9999999999"),))  # a duplicate stored row must not keep a stale value
    wired["sub"] = _sub(values=stored)
    adv = _adv("RETURNED")
    master = NS(account_no="5556667778", account_name="สมชาย ใหม่", status="ACTIVE")
    db = _Db(adv=adv, requester=REQUESTER, master=master)
    ar.resubmit_advance("ADV-2610-001", ResubmitIn(action_by="E1"), BackgroundTasks(), db)
    assert db.committed and adv.fin_status == "RESUBMITTED"
    got = [(r.question_id, r.value_text) for r in stored if r.question_id in (3, 4, 5)]
    assert got == [(3, "KBANK"), (4, "5556667778"), (5, "สมชาย ใหม่"), (4, "5556667778")]
    assert db.of(FormSubmissionValue) == []  # the stored rows are rewritten, none added
    assert wired["describe"] == [("E1", Decimal("15000"))]


@pytest.mark.parametrize("master", [None, NS(account_no="5556667778", account_name="ก", status="INACTIVE")])
def test_resubmit_self_without_active_master_is_400(wired, master):
    stored = _self_values()
    wired["sub"] = _sub(values=stored)
    adv = _adv("RETURNED")
    db = _Db(adv=adv, requester=REQUESTER, master=master)
    with pytest.raises(HTTPException) as ei:
        ar.resubmit_advance("ADV-2610-001", ResubmitIn(action_by="E1"), BackgroundTasks(), db)
    assert (ei.value.status_code, ei.value.detail) == (400, "ยังไม่มีบัญชีรับเงินที่บัญชีอนุมัติ — กรุณาขอเพิ่มบัญชีรับเงิน")
    assert adv.fin_status == "RETURNED" and db.added == [] and not db.committed
    assert [r.value_text for r in stored if r.question_id == 4] == ["1112223334"]


def test_resubmit_leaves_supplier_values_untouched(wired):
    stored = _supplier_values()
    stored[3].value_text = "123-456-7890"  # even a not-yet-normalized stored account is left as stored
    wired["sub"] = _sub(values=stored)
    before = [(r.question_id, r.value_text, r.value_number, r.value_date) for r in stored]
    adv = _adv("RETURNED")
    db = _Db(adv=adv, requester=REQUESTER, master=NS(account_no="5556667778", account_name="x", status="ACTIVE"))
    ar.resubmit_advance("ADV-2610-001", ResubmitIn(action_by="E1"), BackgroundTasks(), db)
    assert db.committed and adv.fin_status == "RESUBMITTED"
    assert [(r.question_id, r.value_text, r.value_number, r.value_date) for r in stored] == before
    assert db.of(FormSubmissionValue) == []


def test_resubmit_bad_stored_supplier_bank_is_400(wired):
    stored = _supplier_values()
    stored[2].value_text = "NOPE"
    wired["sub"] = _sub(values=stored)
    adv = _adv("RETURNED")
    db = _Db(adv=adv, requester=REQUESTER)
    with pytest.raises(HTTPException) as ei:
        ar.resubmit_advance("ADV-2610-001", ResubmitIn(action_by="E1"), BackgroundTasks(), db)
    assert ei.value.status_code == 400 and "กรุณาเลือกธนาคารจากรายการ" in ei.value.detail
    assert adv.fin_status == "RETURNED" and not db.committed


def test_resubmit_refusals_happen_before_the_lock(wired):
    db = _Db(adv=_adv("RETURNED"), requester=REQUESTER)
    with pytest.raises(HTTPException):
        ar.resubmit_advance("ADV-2610-001", ResubmitIn(action_by="F1"), BackgroundTasks(), db)
    assert wired["locked"] == []


class _LockQ:
    def __init__(self, db, entities):
        self.db, self.call = db, {"entities": entities, "steps": []}
        db.calls.append(self.call)

    def __getattr__(self, name):
        def step(*a):
            self.call["steps"].append(name)
            return self.db.result if name == "one" else self

        return step


class _LockDb:
    def __init__(self, result):
        self.result, self.calls = result, []

    def query(self, *entities):
        return _LockQ(self, entities)


def test_lock_submission_with_values_locks_then_reloads():
    from services.finance import advance_repo
    loaded = NS(id=5)
    db = _LockDb(loaded)
    assert advance_repo.lock_submission_with_values(db, 5) is loaded
    lock, load = db.calls
    # the edit path's column-only FOR UPDATE first (no eager outer joins under FOR UPDATE) …
    assert lock["entities"] == (FormSubmission.status_approve, FormSubmission.current_approval_level)
    assert lock["steps"] == ["filter", "with_for_update", "one"]
    # … then the full row with values + form questions, refreshed from the DB, not locked again
    assert load["entities"] == (FormSubmission,)
    assert load["steps"] == ["options", "filter", "populate_existing", "one"]


def test_resubmit_unknown_requester_user(wired):
    db = _Db(adv=_adv("RETURNED"), requester=None)
    with pytest.raises(HTTPException) as ei:
        ar.resubmit_advance("ADV-2610-001", ResubmitIn(action_by="E1"), BackgroundTasks(), db)
    assert ei.value.status_code == 403 and db.added == []


def test_markers_written_by_routes_start_a_new_round(wired):
    """Review Focus #2: the markers the routes write make round-1 approvals invisible to round 2."""
    adv = _adv("VOUCHERED")
    db = _Db(adv=adv, requester=REQUESTER)
    ar.return_advance("ADV-2610-001", ReturnIn(action_by="F1", remark="x"), BackgroundTasks(), db)
    ar.resubmit_advance("ADV-2610-001", ResubmitIn(action_by="E1"), BackgroundTasks(), db)
    markers = [_log(10 + n, m.level_no, m.action, m.action_by) for n, m in enumerate(db.of(FormApprovalLog))]
    assert [(m["action"], m["level_no"]) for m in markers] == [("RETURNED", 0), ("RESUBMITTED", 0)]
    current = rules.current_round(ROUND_1[:2] + markers)
    assert current == [] and rules.step_approvals(current) == [] and not rules.leader_approved(current, 1)


def test_voucher_after_reapproval_goes_to_awaiting_payment(wired):
    """RESUBMITTED + Approved = รอตั้งเบิกทำจ่าย; the first voucher reuses the row and moves to VOUCHERED."""
    from schemas.finance_schema import VoucherIn
    adv = _adv("RESUBMITTED")
    db = _Db(adv=adv)
    ar.voucher_advance("ADV-2610-001", VoucherIn(action_by="F1", voucher_no="SADV-2", voucher_date="2026-10-06"), db)
    assert (adv.fin_status, adv.voucher_no) == ("VOUCHERED", "SADV-2")
    (log,) = db.of(FinAdvanceLog)
    assert log.action == "VOUCHER" and log.changes == {"voucher_no": [None, "SADV-2"],
                                                         "voucher_date": [None, "2026-10-06"]}


@pytest.mark.parametrize("route,body", [
    ("voucher_advance", {"voucher_date": "2026-10-06"}),
    ("pay_advance", {"amount_paid": "100", "transfer_date": "2026-10-06"}),
])
def test_finance_cannot_continue_while_returned(wired, route, body):
    from schemas.finance_schema import PayIn, VoucherIn
    schema = VoucherIn if route == "voucher_advance" else PayIn
    db = _Db(adv=_adv("RETURNED"))
    with pytest.raises(HTTPException) as ei:
        args = ("ADV-2610-001", schema(action_by="F1", **body))
        getattr(ar, route)(*args, db) if route == "voucher_advance" else getattr(ar, route)(*args, BackgroundTasks(), db)
    assert ei.value.status_code == 409


# ---------------------------- edit lock in update_form_details ----------------------------

class _StateQ:
    def __init__(self, db, entity):
        self.db, self.entity = db, entity

    def filter(self, *a):
        return self

    def with_for_update(self):
        self.db.locked = True
        return self

    def order_by(self, *a):
        return self

    def one(self):
        return (self.db.status_approve, self.db.level)

    def scalar(self):
        return self.db.fin_status

    def all(self):
        self.db.log_reads += 1
        return [NS(submission_id=9, **log) for log in self.db.logs]


class _StateDb:
    def __init__(self, status_approve, level=1, fin_status=None, logs=()):
        self.status_approve, self.level, self.fin_status, self.logs = status_approve, level, fin_status, list(logs)
        self.locked, self.log_reads, self.entities = False, 0, []

    def query(self, *entities):
        self.entities.append(entities)
        return _StateQ(self, entities)


def test_edit_state_locks_and_reads_fresh_values():
    db = _StateDb("In Progress")
    assert sr._advance_edit_state(db, NS(id=9)) == (L.PENDING_APPROVAL, False)
    assert db.locked
    assert db.entities[0] == (FormSubmission.status_approve, FormSubmission.current_approval_level)


def test_edit_state_step1_approved_in_current_round():
    assert sr._advance_edit_state(_StateDb("In Progress", 2, logs=[_log(1, 1, "APPROVED", 101)]), NS(id=9)) == \
        (L.PENDING_APPROVAL, True)
    # approved in round 1 only, then returned + resubmitted: editable again until round-2 step 1
    logs = ROUND_1 + [_log(4, 0, "RESUBMITTED", 7)]
    assert sr._advance_edit_state(_StateDb("In Progress", 1, "RESUBMITTED", logs), NS(id=9)) == \
        (L.PENDING_APPROVAL, False)
    logs.append(_log(5, 1, "APPROVED", 101))
    assert sr._advance_edit_state(_StateDb("In Progress", 2, "RESUBMITTED", logs), NS(id=9)) == \
        (L.PENDING_APPROVAL, True)


@pytest.mark.parametrize("status_approve,fin_status,expected", [
    ("Approved", "RETURNED", L.RETURNED),
    ("Approved", None, L.AWAITING_VOUCHER),
    ("Approved", "VOUCHERED", L.AWAITING_PAYMENT),
    ("Rejected", None, L.REJECTED),
])
def test_edit_state_outside_approval_skips_round_logs(status_approve, fin_status, expected):
    db = _StateDb(status_approve, fin_status=fin_status, logs=[_log(1, 1, "APPROVED", 101)])
    assert sr._advance_edit_state(db, NS(id=9)) == (expected, False)
    assert db.log_reads == 0


MASTER = NS(account_no="1112223334", account_name="สมชาย", status="ACTIVE")


def _edit_case(monkeypatch, state, status_approve="Approved", stored_extra=()):
    """update_form_details on a fake ADV (questions: 1 amount, 2 payee type, 3 bank, 4 account no,
    5 account name, 6 use date) with the edit state forced to `state`."""
    monkeypatch.setattr(sr.approval_repo, "describe", lambda *a: None)
    monkeypatch.setattr(sr, "_advance_edit_state", lambda db, sub: state)
    names = ["adv_amount", "adv_payee_type", "adv_bank", "adv_account_no", "adv_account_name", "adv_use_date"]
    types = ["number", "dropdown", "select", "text", "text", "date"]
    form = NS(form_type=sr.ADVANCE_FORM_TYPE, version=1,
              questions=[NS(id=i + 1, question_name=n, question_type=t, sort_order=i + 1, is_required=False,
                            question_label=n) for i, (n, t) in enumerate(zip(names, types))])

    def value(qid, text=None, number=None, d=None):
        return NS(question_id=qid, value_text=text, value_number=number, value_date=d, value_boolean=None)

    stored = [value(1, number=100), value(2, "SUPPLIER"), value(3, "SCB"), value(4, "9999999999"),
              value(5, "Supplier Co"), value(6, d=date.today() + timedelta(days=5)), *stored_extra]
    sub = NS(status="Open", status_approve=status_approve, created_by="E1", form=form, values=stored, id=9,
             form_id="ADV-2610-001", form_master_id=1, updated_by=None)

    class Q:
        def __init__(self, result):
            self.result = result

        def options(self, *a):
            return self

        def filter(self, *a):
            return self

        def first(self):
            return self.result

    class Db:
        def __init__(self):
            self.added, self.committed = [], False

        def query(self, model):
            return Q(sub if model is sr.FormSubmission else MASTER)

        def add(self, obj):
            self.added.append(obj)

        def commit(self):
            self.committed = True

        def rollback(self):
            pass

    return sub, stored, Db(), value


def test_returned_owner_edit_runs_self_guard(monkeypatch):
    """Review Focus #3: at RETURNED the creator edits, and SELF still overwrites the account from the master."""
    sub, stored, db, value = _edit_case(monkeypatch, (L.RETURNED, False))
    payload = NS(updated_by="E1", values=[value(2, "SELF"), value(1, number=2500)])
    out = sr.update_form_details("ADV-2610-001", payload, db)
    assert out["form_id"] == "ADV-2610-001" and db.committed
    got = {r.question_id: (r.value_text, r.value_number) for r in stored}
    assert got[2][0] == "SELF" and got[1][1] == 2500
    assert (got[3][0], got[4][0], got[5][0]) == ("KBANK", "1112223334", "สมชาย")


def test_returned_supplier_edit_normalizes_account(monkeypatch):
    sub, stored, db, value = _edit_case(monkeypatch, (L.RETURNED, False))
    sr.update_form_details("ADV-2610-001", NS(updated_by="E1", values=[value(4, "123-456-7890")]), db)
    assert next(r for r in stored if r.question_id == 4).value_text == "1234567890"


def test_in_progress_before_step1_still_editable(monkeypatch):
    sub, stored, db, value = _edit_case(monkeypatch, (L.PENDING_APPROVAL, False), status_approve="In Progress")
    sr.update_form_details("ADV-2610-001", NS(updated_by="E1", values=[value(1, number=900)]), db)
    assert db.committed and stored[0].value_number == 900


@pytest.mark.parametrize("state,status_code,detail", [
    ((L.PENDING_APPROVAL, True), 409, L.EDIT_LOCKED_STEP1),
    ((L.AWAITING_VOUCHER, False), 409, L.EDIT_LOCKED),
    ((L.AWAITING_PAYMENT, False), 409, L.EDIT_LOCKED),
    ((L.AWAITING_CLEARING, False), 409, L.EDIT_LOCKED),
    ((L.REJECTED, False), 409, L.EDIT_LOCKED),
])
def test_edit_locked_states(monkeypatch, state, status_code, detail):
    sub, stored, db, value = _edit_case(monkeypatch, state)
    with pytest.raises(HTTPException) as ei:
        sr.update_form_details("ADV-2610-001", NS(updated_by="E1", values=[value(1, number=1)]), db)
    assert (ei.value.status_code, ei.value.detail) == (status_code, detail)
    assert stored[0].value_number == 100 and db.added == [] and not db.committed


@pytest.mark.parametrize("state", [(L.RETURNED, False), (L.PENDING_APPROVAL, False)])
def test_edit_by_someone_else_is_403(monkeypatch, state):
    sub, stored, db, value = _edit_case(monkeypatch, state)
    with pytest.raises(HTTPException) as ei:
        sr.update_form_details("ADV-2610-001", NS(updated_by="X9", values=[value(1, number=1)]), db)
    assert ei.value.status_code == 403 and stored[0].value_number == 100 and not db.committed


def test_returned_edit_keeps_duplicate_row_check(monkeypatch):
    sub, stored, db, value = _edit_case(monkeypatch, (L.RETURNED, False))
    payload = NS(updated_by="E1", values=[value(4, "1111111111"), value(4, "2222222222")])
    with pytest.raises(HTTPException) as ei:
        sr.update_form_details("ADV-2610-001", payload, db)
    assert ei.value.status_code == 400 and ei.value.detail == sr._DUPLICATE_VALUES and not db.committed
