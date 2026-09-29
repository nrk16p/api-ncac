"""Finance — เบิกเงิน Advance.

ยังไม่มี auth เหมือน route อื่นของ api-ncac ในตอนนี้ (ใช้ทดสอบ local เท่านั้น) — ต้องทำ launch gate
ด้านความปลอดภัยก่อนขึ้น production ดู spec §9 ใน menait-service
"""
import os
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from models.finance_model import FinAccount, FinAdvance, FinAdvanceLog
from models.user_model import User
from schemas.finance_schema import AccountCreate, AccountUpdate, ClearIn, ConfirmIn, PayIn, SendBackIn, VoucherIn
from services.finance import advance_logic as logic
from services.finance import advance_repo as repo
from services.finance import approval_repo

router = APIRouter(prefix="/finance", tags=["Finance - Advance"])


def _finance_ids():
    depts = [int(x) for x in logic.parse_id_list(os.getenv("FINANCE_DEPARTMENT_IDS", "4,6")) if x.isdigit()]
    emps = logic.parse_id_list(os.getenv("FINANCE_EMPLOYEE_IDS"))
    return depts, emps


def require_finance(db: Session, employee_id: str) -> User:
    user = db.query(User).filter(User.employee_id == employee_id).first()
    depts, emps = _finance_ids()
    if user is None or not logic.is_finance_user(user.department_id, user.employee_id, depts, emps):
        raise HTTPException(status_code=403, detail="เฉพาะฝ่ายการเงินเท่านั้น")
    return user


def _rule_error(exc: logic.AdvanceRuleError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=str(exc))


def _account_dict(acc: FinAccount) -> dict:
    return {"acc_code": acc.acc_code, "acc_name": acc.acc_name, "acc_name_en": acc.acc_name_en,
            "is_active": acc.is_active}


# ---------------------------- accounts ----------------------------

@router.get("/accounts")
def list_accounts(active: Optional[bool] = None, db: Session = Depends(get_db)):
    query = db.query(FinAccount)
    if active is not None:
        query = query.filter(FinAccount.is_active == active)
    return [_account_dict(a) for a in query.order_by(FinAccount.acc_code).all()]


@router.post("/accounts", status_code=201)
def create_account(body: AccountCreate, db: Session = Depends(get_db)):
    require_finance(db, body.action_by)
    if db.get(FinAccount, body.acc_code) is not None:
        raise HTTPException(status_code=409, detail="รหัสบัญชีนี้มีอยู่แล้ว")
    acc = FinAccount(acc_code=body.acc_code, acc_name=body.acc_name, acc_name_en=body.acc_name_en, is_active=True)
    db.add(acc)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="รหัสบัญชีนี้มีอยู่แล้ว")
    return _account_dict(acc)


@router.put("/accounts/{acc_code}")
def update_account(acc_code: str, body: AccountUpdate, db: Session = Depends(get_db)):
    require_finance(db, body.action_by)
    acc = db.get(FinAccount, acc_code)
    if acc is None:
        raise HTTPException(status_code=404, detail="ไม่พบรหัสบัญชี")
    for field in ("acc_name", "acc_name_en", "is_active"):
        value = getattr(body, field)
        if value is not None:
            setattr(acc, field, value)
    db.commit()
    return _account_dict(acc)


# ---------------------------- advances (read) ----------------------------

@router.get("/advances")
def list_advances(
    status: Optional[str] = None,
    overdue: Optional[bool] = None,
    employee_id: Optional[str] = None,
    acc_code: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
):
    return repo.list_advances(db, status=status, overdue=overdue, employee_id=employee_id,
                              acc_code=acc_code, date_from=date_from, date_to=date_to)


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    return repo.outstanding_summary(db)


@router.get("/approval-tiers")
def approval_tiers(db: Session = Depends(get_db)):
    return approval_repo.list_tiers(db)


@router.get("/approval-preview")
def approval_preview(employee_id: str, amount: str, db: Session = Depends(get_db)):
    try:
        result = approval_repo.describe(db, employee_id, amount)
    except logic.AdvanceRuleError as exc:
        raise _rule_error(exc)
    return {"clause": result["clause"], "approver_label": result["approver_label"],
            "required_level": result["required_level"]}


@router.get("/approvals/pending")
def approvals_pending(employee_id: str, db: Session = Depends(get_db)):
    return approval_repo.pending_for(db, employee_id)


@router.get("/advances/{form_id}")
def get_advance(form_id: str, db: Session = Depends(get_db)):
    detail = repo.get_advance_detail(db, form_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ไม่พบรายการเบิกเงิน")
    try:
        result = approval_repo.describe(db, detail["requester"]["employee_id"], detail["request"]["amount"])
        detail["approval"] = {"clause": result["clause"], "approver_label": result["approver_label"],
                              "required_level": result["required_level"]}
    except logic.AdvanceRuleError:
        detail["approval"] = None
    return detail


# ---------------------------- advances (write) ----------------------------

PAY_FIELDS = ("acc_code", "payment_doc_no", "purpose", "amount_paid", "transfer_date", "clear_due_date")
VOUCHER_FIELDS = ("voucher_no", "voucher_date")
CLEAR_FIELDS = ("clear_date", "amount_actual", "clear_doc_no", "settle_amount", "settle_date", "remark")

_ALREADY_SAVED = "รายการนี้ถูกบันทึกไปแล้ว กรุณารีเฟรชหน้าจอ"


def _snapshot(adv, fields):
    return {field: getattr(adv, field) for field in fields} if adv is not None else {}


def _pay_values(body, due):
    return {
        "acc_code": body.acc_code,
        "payment_doc_no": body.payment_doc_no,
        "purpose": body.purpose,
        "amount_paid": body.amount_paid,
        "transfer_date": body.transfer_date,
        "clear_due_date": due,
    }


def _load_for_update(db: Session, form_id: str):
    sub = repo.get_advance_submission(db, form_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="ไม่พบรายการเบิกเงิน")
    adv = db.query(FinAdvance).filter(FinAdvance.submission_id == sub.id).with_for_update().first()
    status, _ = logic.derive_status(
        sub.status_approve,
        adv.fin_status if adv is not None else None,
        adv.clear_due_date if adv is not None else None,
        repo.today_bkk(),
    )
    return sub, adv, status


def _commit_and_return(db: Session, form_id: str):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=_ALREADY_SAVED)
    return repo.get_advance_detail(db, form_id)


@router.put("/advances/{form_id}/voucher")
def voucher_advance(form_id: str, body: VoucherIn, db: Session = Depends(get_db)):
    require_finance(db, body.action_by)
    sub, adv, status = _load_for_update(db, form_id)
    try:
        logic.check_voucher(status, voucher_date=body.voucher_date, is_edit=body.is_edit)
    except logic.AdvanceRuleError as exc:
        raise _rule_error(exc)
    values = {"voucher_no": body.voucher_no, "voucher_date": body.voucher_date}
    before = _snapshot(adv, VOUCHER_FIELDS)
    action = "VOUCHER_EDIT" if adv is not None else "VOUCHER"
    if adv is None:
        adv = FinAdvance(submission_id=sub.id, form_id=sub.form_id, fin_status=logic.FIN_VOUCHERED)
        db.add(adv)
    for field, value in values.items():
        setattr(adv, field, value)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=_ALREADY_SAVED)
    db.add(FinAdvanceLog(advance_id=adv.id, action=action, changes=logic.diff_fields(before, values),
                         action_by=body.action_by))
    return _commit_and_return(db, form_id)


@router.put("/advances/{form_id}/pay")
def pay_advance(form_id: str, body: PayIn, db: Session = Depends(get_db)):
    require_finance(db, body.action_by)
    sub, adv, status = _load_for_update(db, form_id)
    account = db.get(FinAccount, body.acc_code) if body.acc_code else None
    acc_active = True if not body.acc_code else bool(account and account.is_active)
    try:
        due = logic.check_pay(status, acc_active=acc_active, amount_paid=body.amount_paid,
                              transfer_date=body.transfer_date, clear_due_date=body.clear_due_date,
                              is_edit=body.is_edit)
    except logic.AdvanceRuleError as exc:
        raise _rule_error(exc)

    if adv is None:  # unreachable: check_pay rejects AWAITING_VOUCHER — defensive
        raise HTTPException(status_code=409, detail="ยังไม่ได้ตั้งเบิก กรุณาตั้งเบิกก่อนจ่ายเงิน")
    values = _pay_values(body, due)
    if body.acc_code is None:
        values["acc_code"] = adv.acc_code
    before = _snapshot(adv, PAY_FIELDS)
    action = "PAY" if status == logic.AWAITING_PAYMENT else "PAY_EDIT"
    if status == logic.AWAITING_PAYMENT:
        adv.fin_status = logic.FIN_PAID
        adv.paid_by = body.action_by
        adv.paid_at = func.now()
    for field, value in values.items():
        setattr(adv, field, value)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=_ALREADY_SAVED)
    db.add(FinAdvanceLog(advance_id=adv.id, action=action, changes=logic.diff_fields(before, values),
                         action_by=body.action_by))
    return _commit_and_return(db, form_id)


@router.put("/advances/{form_id}/clear")
def clear_advance(form_id: str, body: ClearIn, db: Session = Depends(get_db)):
    sub, adv, status = _load_for_update(db, form_id)
    if adv is None:
        raise HTTPException(status_code=409, detail="การเงินยังไม่ได้จ่ายเงิน จึงยังเคลียร์ไม่ได้")
    try:
        settle, settle_date = logic.check_clear(
            status, is_owner=(body.action_by == sub.created_by), amount_paid=adv.amount_paid,
            clear_date=body.clear_date, amount_actual=body.amount_actual, settle_date=body.settle_date)
    except logic.AdvanceRuleError as exc:
        raise _rule_error(exc)

    values = {"clear_date": body.clear_date, "amount_actual": body.amount_actual, "clear_doc_no": body.clear_doc_no,
              "settle_amount": settle, "settle_date": settle_date, "remark": body.remark}
    before = _snapshot(adv, CLEAR_FIELDS)
    action = "CLEAR_EDIT" if status == logic.AWAITING_REVIEW else "CLEAR_SUBMIT"
    for field, value in values.items():
        setattr(adv, field, value)
    adv.fin_status = logic.FIN_CLEARING_SUBMITTED
    adv.clear_submitted_at = func.now()
    db.add(FinAdvanceLog(advance_id=adv.id, action=action, changes=logic.diff_fields(before, values),
                         remark=body.remark, action_by=body.action_by))
    return _commit_and_return(db, form_id)


@router.put("/advances/{form_id}/send-back")
def send_back_advance(form_id: str, body: SendBackIn, db: Session = Depends(get_db)):
    require_finance(db, body.action_by)
    _, adv, status = _load_for_update(db, form_id)
    try:
        logic.check_fresh(body.expected_clear_submitted_at, adv.clear_submitted_at if adv is not None else None)
        logic.check_send_back(status, review_remark=body.review_remark)
    except logic.AdvanceRuleError as exc:
        raise _rule_error(exc)
    adv.fin_status = logic.FIN_SENT_BACK
    adv.review_remark = body.review_remark
    db.add(FinAdvanceLog(advance_id=adv.id, action="SEND_BACK", remark=body.review_remark,
                         action_by=body.action_by))
    return _commit_and_return(db, form_id)


@router.put("/advances/{form_id}/confirm")
def confirm_advance(form_id: str, body: ConfirmIn, db: Session = Depends(get_db)):
    require_finance(db, body.action_by)
    _, adv, status = _load_for_update(db, form_id)
    try:
        logic.check_fresh(body.expected_clear_submitted_at, adv.clear_submitted_at if adv is not None else None)
        extra_paid_on = logic.check_confirm(status, settle_amount=adv.settle_amount if adv is not None else None,
                                            settle_date=body.settle_date)
    except logic.AdvanceRuleError as exc:
        raise _rule_error(exc)
    changes = {}
    if extra_paid_on is not None:
        changes = logic.diff_fields({"settle_date": adv.settle_date}, {"settle_date": extra_paid_on})
        adv.settle_date = extra_paid_on
    adv.fin_status = logic.FIN_CLOSED
    adv.closed_by = body.action_by
    adv.closed_at = func.now()
    db.add(FinAdvanceLog(advance_id=adv.id, action="CONFIRM", changes=changes or None, action_by=body.action_by))
    return _commit_and_return(db, form_id)
