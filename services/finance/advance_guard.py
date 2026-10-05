"""ADV value guard (spec v2 + v3 §6): one implementation for submit, the requester's edit (PUT /forms/{id}) and
resubmit (PUT /finance/advances/{id}/resubmit).

* `check_values` validates a full value set: use-date ≥ today (Bangkok), payee (SELF → the requester's approved
  master overwrites bank / account / name; SUPPLIER → known bank + normalized account number) and an eligible
  approver for the amount (`approval_repo.describe`).
* `apply` runs `check_values` on a stored submission with edits merged over it, then writes back what the guard
  normalized or forced.

Both raise `advance_logic.AdvanceRuleError`; the routes map it to HTTP 400.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from models.finance_model import FinPayeeAccount
from models.master_model import FormSubmissionValue
from services.finance import advance_logic, approval_repo, payee_logic

SELF_PAYEE_INCOMPLETE = "ข้อมูลบัญชีรับเงินไม่ครบ (ธนาคาร/เลขที่บัญชี/ชื่อบัญชี) — กรุณาโหลดหน้าใหม่แล้วส่งอีกครั้ง"

_SELF_FIELDS = (("adv_bank", "bank"), ("adv_account_no", "account_no"), ("adv_account_name", "account_name"))


def today_bkk():
    return datetime.now(ZoneInfo("Asia/Bangkok")).date()


def check_values(db, form, values, created_by) -> set:
    """`values`: objects with question_id / value_text / value_number / value_date. SELF payee values and a
    SUPPLIER account number are written back into `values`. Returns the question ids the SELF master overwrote."""
    questions = [{"id": q.id, "name": q.question_name, "type": q.question_type, "sort_order": q.sort_order}
                 for q in form.questions]
    raw_values = [{"question_id": v.question_id, "value_text": v.value_text, "value_number": v.value_number,
                   "value_date": v.value_date} for v in values]
    overwritten = set()
    use_date = advance_logic.find_use_date(questions, raw_values)
    advance_logic.check_use_date(use_date, today_bkk())
    payee = None
    if any(q.question_name == "adv_payee_type" for q in form.questions):
        payee_type = advance_logic.submitted_value(questions, raw_values, "adv_payee_type", (), "value_text")
        master = None
        if payee_type == payee_logic.PAYEE_SELF:
            master = (db.query(FinPayeeAccount)
                      .filter(FinPayeeAccount.employee_id == created_by).first())
        payee = payee_logic.resolve_payee(payee_type, master)
    if payee is not None:
        # SELF: the master is the only source of truth — overwrite whatever the client sent
        for name, key in _SELF_FIELDS:
            question = next((q for q in form.questions if q.question_name == name), None)
            matches = [v for v in values if question is not None and v.question_id == question.id]
            if not matches:
                raise advance_logic.AdvanceRuleError(SELF_PAYEE_INCOMPLETE)
            for value in matches:  # every matching row, not just the first
                value.value_text = payee[key]
            overwritten.add(question.id)
    else:
        bank_q = next((q for q in form.questions if q.question_name == "adv_bank"), None)
        bank = advance_logic.submitted_value(questions, raw_values, "adv_bank", (), "value_text")
        if bank_q is not None:
            advance_logic.check_bank(bank)
        account_q = next((q for q in form.questions if q.question_name == "adv_account_no"), None)
        if account_q is not None:
            account_value = next((v for v in values if v.question_id == account_q.id), None)
            if account_value is None:
                raise advance_logic.account_error(bank)
            account_value.value_text = advance_logic.check_account_no(bank, account_value.value_text)
    amount = advance_logic.submitted_value(questions, raw_values, "adv_amount", ("number",), "value_number")
    approval_repo.describe(db, created_by, amount)
    return overwritten


def _copy(value):
    return SimpleNamespace(question_id=value.question_id, value_text=value.value_text,
                           value_number=value.value_number, value_date=value.value_date)


def apply(db, submission, payload_values) -> list:
    """Guard a stored ADV `submission` with `payload_values` (the edit; [] for a resubmit) merged over its stored
    values, then write back:

    * into `payload_values`: the guard's result (normalized account number / SELF snapshot), so the caller's own
      merge persists it;
    * into the stored rows: the SELF master snapshot, for every stored row of an overwritten question (a duplicate
      row must not keep a stale or forged value), adding the row when none is stored.

    A SUPPLIER value that is not in the payload is never rewritten. Raises AdvanceRuleError, before writing
    anything. Returns the merged post-guard values (question_id / value_* objects)."""
    merged = {v.question_id: _copy(v) for v in submission.values}
    for v in payload_values:
        merged[v.question_id] = _copy(v)
    overwritten = check_values(db, submission.form, list(merged.values()), submission.created_by)
    for v in payload_values:  # persist the normalized account number
        v.value_text = merged[v.question_id].value_text
    # values the guard forced (SELF master snapshot) that are not in the payload must be stored too
    payload_qids = {v.question_id for v in payload_values}
    for qid in overwritten - payload_qids:
        stored = [r for r in submission.values if r.question_id == qid]
        if stored:
            for rec in stored:
                rec.value_text = merged[qid].value_text
        else:
            db.add(FormSubmissionValue(submission_id=submission.id, question_id=qid,
                                       value_text=merged[qid].value_text))
    # a stored duplicate row of a payload question must not keep a stale/forged value
    for qid in overwritten & payload_qids:
        for rec in submission.values:
            if rec.question_id == qid:
                rec.value_text = merged[qid].value_text
    return list(merged.values())
