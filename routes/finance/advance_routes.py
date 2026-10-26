"""Finance — เบิกเงิน Advance.

ยังไม่มี auth เหมือน route อื่นของ api-ncac ในตอนนี้ (ใช้ทดสอบ local เท่านั้น) — ต้องทำ launch gate
ด้านความปลอดภัยก่อนขึ้น production ดู spec §9 ใน menait-service
"""
import logging
import os
from datetime import date
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from models.finance_model import FinClearApprovalLog, FinAccount, FinAdvance, FinAdvanceClearItem, FinAdvanceLog
from models.master_model import FormApprovalLog
from models.user_model import User
from schemas.finance_schema import (
    ClearApproveIn, ClearRejectIn,
    AccountCreate, AccountUpdate, ClearIn, ConfirmIn, OverdueRemindIn, PayIn, RejectVoucherIn, ResubmitIn, ReturnIn, SendBackIn,
    VoucherIn,
)
from services.finance import advance_guard
from services.finance import advance_logic as logic
from services.finance import advance_repo as repo
from services.finance import approval_logic as approval_rules
from services.finance import approval_repo
from services.finance import finance_mail as mail
from services.finance import overdue_reminder
from services.finance import permission_logic as perms
from services.finance import clear_approval_logic as clr
from services.finance import approval_repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/finance", tags=["Finance - Advance"])


def _finance_ids():
    depts = [int(x) for x in logic.parse_id_list(os.getenv("FINANCE_DEPARTMENT_IDS", "4,6")) if x.isdigit()]
    emps = logic.parse_id_list(os.getenv("FINANCE_EMPLOYEE_IDS"))
    return depts, emps


def require_finance(db: Session, employee_id: str) -> User:
    user = db.query(User).filter(User.employee_id == employee_id).first()
    depts, emps = _finance_ids()
    if user is not None and perms.is_superadmin(user.employee_id):
        return user
    if user is None or not logic.is_finance_user(user.department_id, user.employee_id, depts, emps):
        raise HTTPException(status_code=403, detail="เฉพาะฝ่ายการเงินเท่านั้น")
    return user


def require_permission(db: Session, employee_id: str, permission: str) -> User:
    """ฝ่ายการเงิน/บัญชีก่อน แล้วดูแผนก: จ่ายเงิน = แผนกการเงิน ปุ่มอื่น = แผนกบัญชี (ผู้ใช้สั่ง 21/10/2026)"""
    user = require_finance(db, employee_id)
    if not perms.allowed(permission, user.employee_id, user.department_id):
        raise HTTPException(status_code=403, detail=perms.denied_message(permission))
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


# ---------------------------- permissions ----------------------------

@router.get("/permissions/me")
def my_permissions(employee_id: str, db: Session = Depends(get_db)):
    """สิทธิ์ตามหน้าที่ของคนนี้ — หน้าเว็บใช้ซ่อนปุ่มที่กดไม่ได้ (ตัวจริงยังกันที่ endpoint)"""
    user = db.query(User).filter(User.employee_id == employee_id).first()
    depts, emps = _finance_ids()
    is_finance = user is not None and (perms.is_superadmin(user.employee_id)
                                       or logic.is_finance_user(user.department_id, user.employee_id, depts, emps))
    return {
        "employee_id": employee_id,
        "is_finance": is_finance,
        "permissions": perms.permissions_for(user.employee_id, user.department_id) if is_finance else [],
    }


@router.get("/advances/duplicate-doc-no")
def duplicate_doc_no(field: str, value: str, exclude_form_id: Optional[str] = None, db: Session = Depends(get_db)):
    """ใบอื่นที่ใช้เลขเดียวกัน — หน้าเว็บถามก่อนบันทึกเลขที่ตั้งเบิก / เลขที่ทำจ่าย / เลขที่เอกสารเคลียร์
    (ผู้ใช้สั่ง 21/10/2026 แจ้งเมื่อเลขซ้ำ) ไม่บล็อก ให้คนกดยืนยันเองถ้าตั้งใจใช้เลขเดียวกัน"""
    if field not in logic.DOC_NO_FIELDS:
        raise HTTPException(status_code=400, detail="ช่องนี้เช็กเลขซ้ำไม่ได้")
    text = (value or "").strip()
    if not text:
        return {"field": field, "value": text, "form_ids": []}
    column = getattr(FinAdvance, field)
    query = db.query(FinAdvance.form_id).filter(func.upper(func.trim(column)) == text.upper())
    if exclude_form_id:
        query = query.filter(FinAdvance.form_id != exclude_form_id)
    rows = query.order_by(FinAdvance.form_id).limit(10).all()
    return {"field": field, "value": text, "form_ids": [row.form_id for row in rows]}


# ---------------------------- advances (read) ----------------------------

@router.get("/advances")
def list_advances(
    status: Optional[str] = None,
    overdue: Optional[bool] = None,
    employee_id: Optional[str] = None,
    acc_code: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    cost_center: Optional[str] = None,
    q: Optional[str] = None,
    department: Optional[str] = None,
    sort: Optional[str] = Query("desc"),
    page: Optional[int] = Query(None, ge=1, le=1_000_000),
    page_size: Optional[str] = Query(str(logic.PAGE_SIZE_DEFAULT)),
    db: Session = Depends(get_db),
):
    """Without `page`: the full array (unchanged; `page_size` and `sort` are ignored, never a 422). With `page`
    (1-based, <= 1,000,000): {items, total, page, page_size, summary, options} (v3 §9) — `status` is a comma list of
    derived statuses, `q` is capped at 100 chars and matched literally (NUL removed). `department` = exact requester
    department name; `sort` = asc|desc on created_at (default desc). `summary` applies the same filters as the list
    except status and overdue (cost_center and department included); only `options` also ignores cost_center and
    department. Paged mode only: page < 1 or > 1,000,000, page_size outside 1..200 (or not a number), sort not
    asc|desc, or an unknown status → 422 (no clamping)."""
    if page is None:
        return repo.list_advances(db, status=status, overdue=overdue, employee_id=employee_id,
                                  acc_code=acc_code, date_from=date_from, date_to=date_to)
    page_size_n = logic.parse_page_size(page_size)
    if page_size_n is None:
        raise HTTPException(status_code=422, detail=f"page_size ต้องเป็นตัวเลข {1}..{logic.PAGE_SIZE_MAX}")
    sort = "desc" if sort is None else sort
    if sort not in ("asc", "desc"):
        raise HTTPException(status_code=422, detail="sort ต้องเป็น asc หรือ desc")
    statuses, unknown = logic.parse_status_list(status)
    if unknown:
        raise HTTPException(status_code=422, detail=f"สถานะไม่ถูกต้อง: {', '.join(unknown)}")
    return repo.list_advances_page(
        db, page=page, page_size=page_size_n, statuses=statuses, overdue=overdue, employee_id=employee_id,
        acc_code=acc_code, date_from=date_from, date_to=date_to, cost_center=logic.clean_text(cost_center), q=logic.clean_q(q),
        department=logic.clean_text(department), sort=sort)


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
            "required_level": result["required_level"], "steps": result["steps"]}


@router.get("/approvals/pending")
def approvals_pending(employee_id: str, db: Session = Depends(get_db)):
    return approval_repo.pending_for(db, employee_id)


@router.get("/advances/{form_id}")
def get_advance(form_id: str, db: Session = Depends(get_db)):
    detail = repo.get_advance_detail(db, form_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ไม่พบรายการเบิกเงิน")
    try:
        # clause, approver_label, required_level (final step), steps, current_step, step_approvals (current round)
        detail["approval"] = approval_repo.detail_approval(
            db, repo.get_advance_submission(db, form_id), detail["request"]["amount"])
    except logic.AdvanceRuleError:
        detail["approval"] = None

    # ด่านอนุมัติการเคลียร์ (19/10/2026) — มีค่าเฉพาะตอนรออนุมัติเคลียร์
    detail["clear_approval"] = None
    if detail.get("status") == logic.CLEARING_APPROVAL:
        sub = repo.get_advance_submission(db, form_id)
        adv = db.query(FinAdvance).filter(FinAdvance.submission_id == sub.id).first()
        try:
            info, _ctx = _clear_plan(db, sub)
            step = _clear_step(adv, info)
            detail["clear_approval"] = {
                "step": step,
                "total_steps": len(info["steps"]),
                "clause": info["clause"],
                "required_level": clr.step_spec(info, step)["required_level"],
            }
        except logic.AdvanceRuleError:
            pass
    return detail


@router.get("/advances/{form_id}/approvers")
def advance_approvers(form_id: str, db: Session = Depends(get_db)):
    sub = repo.get_advance_submission(db, form_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="ไม่พบรายการเบิกเงิน")
    try:
        return approval_repo.suggested_approvers(db, sub)
    except logic.AdvanceRuleError as exc:
        raise _rule_error(exc)


# ---------------------------- advances (write) ----------------------------

PAY_FIELDS = ("acc_code", "payment_doc_no", "purpose", "amount_paid", "transfer_date", "clear_due_date")
VOUCHER_FIELDS = ("voucher_no", "voucher_date")
RETURN_FIELDS = ("fin_status", "voucher_no", "voucher_date")
CLEAR_FIELDS = ("clear_date", "amount_actual", "settle_amount", "settle_date", "remark")

_ALREADY_SAVED = "รายการนี้ถูกบันทึกไปแล้ว กรุณารีเฟรชหน้าจอ"
_SQL_NOT_RUN = "ฐานข้อมูลยังไม่รองรับสถานะนี้ — กรุณารัน SQL finance_advance_v3 ก่อน"
_PG_CHECK_VIOLATION = "23514"


def _integrity_error(exc: IntegrityError) -> HTTPException:
    """A CHECK violation (fin_status RETURNED/RESUBMITTED before the v3 SQL ran) is a deployment problem, not a
    concurrent write: 500 with a clear message. Anything else (unique conflicts) stays 409."""
    if getattr(getattr(exc, "orig", None), "pgcode", None) == _PG_CHECK_VIOLATION:
        logger.error("finance advance write hit a CHECK violation (finance_advance_v3 SQL not run?): %s", exc)
        return HTTPException(status_code=500, detail=_SQL_NOT_RUN)
    return HTTPException(status_code=409, detail=_ALREADY_SAVED)


def _snapshot(adv, fields):
    return {field: getattr(adv, field) for field in fields} if adv is not None else {}


def _request_snapshot(form, values) -> dict:
    """The request's logged fields (amount, use_date, purpose, cost_center, bank, account_no, account_name,
    payee_type) from stored/merged answer rows; JSON-safe."""
    rows = []
    for q in form.questions:
        v = next((x for x in values if x.question_id == q.id), None)
        if v is None:
            continue
        rows.append({"name": q.question_name, "type": q.question_type, "sort_order": q.sort_order,
                     "text": v.value_text, "number": v.value_number, "date": v.value_date})
    return {k: logic.jsonable(v) for k, v in logic.pick_request_values(rows).items()}


def _pairs(before: dict, after: dict) -> dict:
    return {k: [before.get(k), v] for k, v in after.items() if before.get(k) != v}


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
    except IntegrityError as exc:
        db.rollback()
        raise _integrity_error(exc)
    return repo.get_advance_detail(db, form_id)


@router.post("/advances/overdue-reminders")
def remind_overdue(body: OverdueRemindIn, db: Session = Depends(get_db)):
    """Finance sends the overdue-clearing reminder now (v3 §7b). Ignores the 7-day interval; counts as sent only
    when SMTP accepted it; logs OVERDUE_REMIND (remark "manual") per sent advance."""
    finance_user = require_permission(db, body.action_by, perms.PERM_OVERDUE_REMIND)
    if not mail.email_enabled():
        return {"sent": 0, "skipped": [], "disabled": True}
    wanted = list(dict.fromkeys(body.form_ids)) if body.form_ids is not None else None
    items, _today = overdue_reminder.load_overdue(db, wanted)
    skipped = []
    if wanted is not None:
        found = {i["form_id"] for i in items}
        skipped = [{"form_id": f, "reason": "ไม่ได้เกินกำหนดเคลียร์"} for f in wanted if f not in found]
    sent, failed = overdue_reminder.send_for_items(db, items, "manual", finance_user.employee_id)
    return {"sent": sent, "skipped": skipped + failed, "disabled": False}


@router.put("/advances/{form_id}/voucher")
def voucher_advance(form_id: str, body: VoucherIn, db: Session = Depends(get_db)):
    require_permission(db, body.action_by, perms.PERM_VOUCHER)
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
    elif (adv.fin_status in (logic.FIN_VOUCHER_REJECTED, logic.FIN_RESUBMITTED)
          and status == logic.AWAITING_VOUCHER):
        # first save after a legacy reject (voucher fields kept) or a re-approved resubmit (fields cleared at
        # return): reuse the row and go to รอจ่าย
        adv.fin_status = logic.FIN_VOUCHERED
        action = "VOUCHER"
    for field, value in values.items():
        setattr(adv, field, value)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _integrity_error(exc)
    db.add(FinAdvanceLog(advance_id=adv.id, action=action, changes=logic.diff_fields(before, values),
                         action_by=body.action_by))
    return _commit_and_return(db, form_id)


@router.put("/advances/{form_id}/return")
def return_advance(form_id: str, body: ReturnIn, background_tasks: BackgroundTasks,
                   db: Session = Depends(get_db)):
    """ตีกลับให้ผู้เบิกแก้ไข (v3 §6) from รอตั้งเบิกทำจ่าย / รอจ่าย, reason required. The fin row goes to RETURNED
    (created when the request was not vouchered yet) with its voucher fields cleared; a RETURN fin log and a
    RETURNED approval-log marker (level_no 0, the Finance user's users.id) record it."""
    finance_user = require_permission(db, body.action_by, perms.PERM_RETURN)
    sub, adv, status = _load_for_update(db, form_id)
    try:
        logic.check_return(status, remark=body.remark)
    except logic.AdvanceRuleError as exc:
        raise _rule_error(exc)
    remark = body.remark.strip()
    values = {"fin_status": logic.FIN_RETURNED, "voucher_no": None, "voucher_date": None}
    before = _snapshot(adv, RETURN_FIELDS)
    if adv is None:  # approved, not vouchered yet: the fin row starts here (pay columns stay NULL)
        adv = FinAdvance(submission_id=sub.id, form_id=sub.form_id, fin_status=logic.FIN_RETURNED)
        db.add(adv)
    for field, value in values.items():
        setattr(adv, field, value)
    try:
        db.flush()
    except IntegrityError as exc:  # a concurrent first voucher/return created the row (or the v3 SQL is missing)
        db.rollback()
        raise _integrity_error(exc)
    changes = logic.diff_fields(before, values)
    # the request's values at the time of the return: the resubmit compares the requester's edits against this
    changes["snapshot"] = _request_snapshot(sub.form, sub.values)
    db.add(FinAdvanceLog(advance_id=adv.id, action="RETURN", changes=changes,
                         remark=remark, action_by=body.action_by))
    db.add(FormApprovalLog(submission_id=sub.id, level_no=0, action=approval_rules.MARKER_RETURNED,
                           action_by=finance_user.id, remark=remark))
    detail = _commit_and_return(db, form_id)
    mail.queue_event(background_tasks, db, mail.RETURNED, sub, remark=remark)
    return detail


@router.put("/advances/{form_id}/resubmit")
def resubmit_advance(form_id: str, body: ResubmitIn, background_tasks: BackgroundTasks,
                     db: Session = Depends(get_db)):
    """แก้ไขและส่งใหม่ (v3 §6): the requester only, at RETURNED only. Back to approval from step 1; the
    RESUBMITTED marker (level_no 0, the requester's users.id) starts a new approval round, so earlier approvals
    no longer count. fin_status RESUBMITTED maps to รอตั้งเบิกทำจ่าย once the request is approved again.
    The stored values go through the full submit/edit guard first: use-date ≥ today, SELF payee re-snapshotted
    from the requester's current master into the stored rows, an eligible approver for the amount (400 if not)."""
    sub, adv, status = _load_for_update(db, form_id)
    try:
        logic.check_resubmit(status, is_owner=(body.action_by == sub.created_by))
    except logic.AdvanceRuleError as exc:
        raise _rule_error(exc)
    requester = db.query(User).filter(User.employee_id == sub.created_by).first()
    if requester is None:
        raise HTTPException(status_code=403, detail="ไม่พบข้อมูลผู้ขอเบิกในระบบ")
    sub = repo.lock_submission_with_values(db, sub.id)  # the edit path's row lock, then fresh values
    pre = _request_snapshot(sub.form, sub.values)  # the requester's edits, before the guard rewrites anything
    try:
        merged = advance_guard.apply(db, sub, [])
    except logic.AdvanceRuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    post = _request_snapshot(sub.form, merged)
    returned = (db.query(FinAdvanceLog)
                .filter(FinAdvanceLog.advance_id == adv.id, FinAdvanceLog.action == "RETURN")
                .order_by(FinAdvanceLog.id.desc()).first())
    base = ((returned.changes or {}).get("snapshot") if returned is not None else None) or pre
    request_changes = _pairs(base, post)  # request field → [value at the return, value now]
    forced = _pairs(pre, post) if post.get("payee_type") == "SELF" else {}
    for key, value in forced.items():  # fields the guard forced (SELF payee) over the edit
        request_changes[f"{key}_forced"] = [pre.get(key), value[1]]
    before = {"fin_status": adv.fin_status, "status_approve": sub.status_approve,
              "current_approval_level": sub.current_approval_level}
    values = {"fin_status": logic.FIN_RESUBMITTED, "status_approve": "In Progress", "current_approval_level": 1}
    adv.fin_status = logic.FIN_RESUBMITTED
    sub.status_approve = "In Progress"
    sub.current_approval_level = 1
    db.add(FinAdvanceLog(advance_id=adv.id, action="RESUBMIT",
                         changes={**request_changes, **logic.diff_fields(before, values)},
                         action_by=body.action_by))
    db.add(FormApprovalLog(submission_id=sub.id, level_no=0, action=approval_rules.MARKER_RESUBMITTED,
                           action_by=requester.id))
    detail = _commit_and_return(db, form_id)
    mail.queue_event(background_tasks, db, mail.RESUBMITTED, sub)
    return detail


@router.put("/advances/{form_id}/reject-voucher")
def reject_voucher(form_id: str, body: RejectVoucherIn, db: Session = Depends(get_db)):
    require_finance(db, body.action_by)
    _, adv, status = _load_for_update(db, form_id)
    try:
        logic.check_reject_voucher(status, remark=body.remark)
    except logic.AdvanceRuleError as exc:
        raise _rule_error(exc)
    adv.fin_status = logic.FIN_VOUCHER_REJECTED
    db.add(FinAdvanceLog(advance_id=adv.id, action="VOUCHER_REJECT", remark=body.remark.strip(),
                         action_by=body.action_by))
    return _commit_and_return(db, form_id)


@router.put("/advances/{form_id}/pay")
def pay_advance(form_id: str, body: PayIn, background_tasks: BackgroundTasks,
                db: Session = Depends(get_db)):
    require_permission(db, body.action_by, perms.PERM_PAY)
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
    except IntegrityError as exc:
        db.rollback()
        raise _integrity_error(exc)
    db.add(FinAdvanceLog(advance_id=adv.id, action=action, changes=logic.diff_fields(before, values),
                         action_by=body.action_by))
    detail = _commit_and_return(db, form_id)
    if action == "PAY":  # the first payment only, not an edit of the payment
        mail.queue_event(background_tasks, db, mail.PAID, sub, amount_paid=adv.amount_paid,
                         transfer_date=adv.transfer_date, clear_due_date=adv.clear_due_date)
    return detail


@router.put("/advances/{form_id}/clear")
def clear_advance(form_id: str, body: ClearIn, background_tasks: BackgroundTasks,
                  db: Session = Depends(get_db)):
    sub, adv, status = _load_for_update(db, form_id)
    if adv is None:
        raise HTTPException(status_code=409, detail="การเงินยังไม่ได้จ่ายเงิน จึงยังเคลียร์ไม่ได้")
    try:
        rows, total_net = logic.check_clear_items(body.items)  # amount_actual = Σ net; client value ignored
        settle, settle_date = logic.check_clear(
            status, is_owner=(body.action_by == sub.created_by), amount_paid=adv.amount_paid,
            clear_date=body.clear_date, amount_actual=total_net, settle_date=body.settle_date)
    except logic.AdvanceRuleError as exc:
        raise _rule_error(exc)

    values = {"clear_date": body.clear_date, "amount_actual": total_net,
              "settle_amount": settle, "settle_date": settle_date, "remark": body.remark}
    before = _snapshot(adv, CLEAR_FIELDS)
    action = "CLEAR_SUBMIT"
    for field, value in values.items():
        setattr(adv, field, value)
    old_total = (db.query(func.sum(FinAdvanceClearItem.net_amount))
                 .filter(FinAdvanceClearItem.advance_id == adv.id).scalar())
    old_count = (db.query(FinAdvanceClearItem).filter(FinAdvanceClearItem.advance_id == adv.id)
                 .delete(synchronize_session=False))
    db.add_all([FinAdvanceClearItem(advance_id=adv.id, **row) for row in rows])
    # ผู้ใช้สั่ง 19/10/2026: ส่งเคลียร์แล้วต้องผ่านหัวหน้าก่อนถึงบัญชี
    # ผู้ใช้สั่ง 26/10/2026: ขั้นอนุมัติเหมือนตอนขอเบิกเสมอ ไม่ว่าใช้จริงเท่าไหร่ ใช้ 0 คืนทั้งก้อนก็ต้องผ่าน
    # (ADV-2610-018 ใช้จริง 0 แล้วข้ามไปรอบัญชีตรวจ) · บัญชีส่งกลับแล้วแก้ส่งใหม่ ขึ้นอนุมัติใหม่เฉพาะตอนยังไม่เคยผ่าน
    try:
        info, clear_ctx = _clear_plan(db, sub)
    except logic.AdvanceRuleError as exc:
        raise _rule_error(exc)
    if clr.needs_reapproval(adv.clear_approved_clause, info["clause"]) and clr.needs_approval(info):
        adv.fin_status = logic.FIN_CLEAR_PENDING
        adv.clear_approval_level = clr.STEP_LEADER
        _clear_log(db, form_id, clr.STEP_LEADER, "SUBMITTED", info["clause"], total_net,
                   action_by=body.action_by)
    else:
        adv.fin_status = logic.FIN_CLEARING_SUBMITTED
        adv.clear_approval_level = None
    adv.clear_submitted_at = func.now()
    changes = {**logic.diff_fields(before, values), "items": [old_count, len(rows)], "items_total": [None if old_total is None else str(old_total), str(total_net)]}
    db.add(FinAdvanceLog(advance_id=adv.id, action=action, changes=changes,
                         remark=body.remark, action_by=body.action_by))
    pending_approval = adv.fin_status == logic.FIN_CLEAR_PENDING
    amount_paid = adv.amount_paid
    detail = _commit_and_return(db, form_id)
    # อีเมลถึงผู้เบิก CC บัญชี ทุกครั้งที่ส่งเคลียร์ บอกว่ารอหัวหน้าหรือรอบัญชีตรวจ (ผู้ใช้สั่ง 20/10/2026)
    mail.queue_event(background_tasks, db, mail.CLEAR_SUBMITTED, sub, amount_paid=amount_paid,
                     amount_actual=total_net, settle_amount=settle, pending=pending_approval,
                     step=clr.STEP_LEADER, total_steps=len(info.get("steps") or []), clause=info.get("clause"))
    if pending_approval:  # หัวหน้าขั้น 1 ต้องรู้ว่ามีใบรอ (ผู้ใช้สั่ง 20/10/2026)
        _queue_clear_pending(background_tasks, db, sub, info, clear_ctx, clr.STEP_LEADER,
                             amount_paid=amount_paid, amount_actual=total_net, settle_amount=settle)
    return detail


def _clear_approver_emails(db: Session, sub, info, ctx, step) -> list:
    """อีเมลคนที่อนุมัติการเคลียร์ขั้นนี้ได้ — กติกาเดียวกับอีเมลขออนุมัติเบิก (หยุดที่ระดับผู้จัดการ ไม่รวมคนที่กดขั้นก่อน)"""
    from models.user_model import User

    spec = clr.step_spec(info, step)
    requester = ctx.people.get(sub.created_by)
    if requester is None:
        return []
    eligible = approval_rules.eligible_approvers(ctx.people.values(), requester, spec["required_level"],
                                                 ctx.mappings, _clear_round_approvers(db, sub.form_id),
                                                 spec.get("approver"))
    eligible = approval_rules.notify_approvers(eligible, spec["required_level"], requester, spec.get("approver"))
    eligible.sort(key=lambda p: (p["level"], p["employee_id"]))
    ids = [p["id"] for p in eligible if p.get("id") is not None]
    by_id = {u.id: u.email for u in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
    return [by_id.get(p.get("id")) for p in eligible if by_id.get(p.get("id"))]


def _queue_clear_pending(background_tasks, db: Session, sub, info, ctx, step, **amounts) -> None:
    """อีเมล รอท่านอนุมัติการเคลียร์ ถึงหัวหน้าขั้นนี้ CC ผู้เบิก — หาผู้รับพลาดต้องไม่ทำให้การเคลียร์ล้ม"""
    try:
        emails = _clear_approver_emails(db, sub, info, ctx, step)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("clear approver emails for %s could not be resolved", sub.form_id)
        return
    mail.queue_event(background_tasks, db, mail.CLEAR_PENDING, sub, approver_emails=emails, step=step,
                     total_steps=len(info.get("steps") or []), clause=info.get("clause"), **amounts)


# ---------------------------- อนุมัติการเคลียร์ (ผู้ใช้สั่ง 19/10/2026) ----------------------------

def _clear_plan(db: Session, sub):
    """สายอนุมัติของการเคลียร์ = สายเดียวกับตอนขอเบิก คิดจากยอดในใบขอ ไม่ใช่ยอดที่ใช้จริง (ผู้ใช้สั่ง 26/10/2026)"""
    ctx = approval_repo.load_context(db, fresh=True)
    ctx = approval_repo.ensure_people(db, ctx, sub.created_by)
    amount = approval_repo._amount_of(db, sub.id)
    if amount is None:
        raise logic.AdvanceRuleError("ไม่พบยอดเงินในใบคำขอเบิก จึงหาผู้อนุมัติการเคลียร์ไม่ได้")
    return clr.plan(ctx.tiers, ctx.people, ctx.mappings, sub.created_by, amount), ctx


def _clear_step(adv, info) -> int:
    """ขั้นที่รออนุมัติเคลียร์อยู่ — ใบที่ค้างตอนสายเปลี่ยน (กติกา/ระดับคนเปลี่ยน) อาจรออยู่เลยขั้นสุดท้ายของสายใหม่
    นับเป็นขั้นสุดท้าย ไม่ให้คิวรออนุมัติพังทั้งหน้า (26/10/2026)"""
    last = max((s["step"] for s in info.get("steps") or ()), default=clr.STEP_LEADER)
    return min(adv.clear_approval_level or clr.STEP_LEADER, last)


def _clear_round_approvers(db: Session, form_id: str) -> tuple:
    """users.id ของคนที่อนุมัติขั้นก่อนหน้าในรอบนี้ (นับจาก SUBMITTED ล่าสุด)"""
    rows = (db.query(FinClearApprovalLog)
            .filter(FinClearApprovalLog.form_id == form_id)
            .order_by(FinClearApprovalLog.id).all())
    start = 0
    for i, r in enumerate(rows):
        if r.action == "SUBMITTED":
            start = i + 1
    ids = []
    for r in rows[start:]:
        if r.action == "APPROVED" and r.action_by:
            user = db.query(User).filter(User.employee_id == r.action_by).first()
            if user is not None:
                ids.append(user.id)
    return tuple(ids)


def _clear_round_steps(db: Session, form_id: str) -> set:
    """ขั้นที่อนุมัติไปแล้วในรอบนี้ (นับจาก SUBMITTED ล่าสุด) — ขั้นที่ข้ามไว้ก่อนแล้วไม่ต้องรอซ้ำ"""
    rows = (db.query(FinClearApprovalLog)
            .filter(FinClearApprovalLog.form_id == form_id)
            .order_by(FinClearApprovalLog.id).all())
    start = 0
    for i, r in enumerate(rows):
        if r.action == "SUBMITTED":
            start = i + 1
    return {r.step for r in rows[start:] if r.action == "APPROVED" and r.step is not None}


def _clear_log(db, form_id, step, action, clause=None, amount=None, remark=None, action_by=None):
    db.add(FinClearApprovalLog(form_id=form_id, step=step, action=action, clause=clause,
                               amount_actual=amount, remark=remark, action_by=action_by))


@router.get("/clear-approvals/pending")
def clear_approvals_pending(employee_id: str, db: Session = Depends(get_db)):
    """ใบที่รอคนนี้อนุมัติการเคลียร์ — หน้าคิวอนุมัติใช้แสดงคู่กับคำขอเบิก"""
    rows = (db.query(FinAdvance).filter(FinAdvance.fin_status == logic.FIN_CLEAR_PENDING).all())
    out = []
    for adv in rows:
        sub = repo.get_advance_submission(db, adv.form_id)
        if sub is None or sub.created_by == employee_id:
            continue
        try:
            info, _ctx = _clear_plan(db, sub)
        except logic.AdvanceRuleError:
            continue
        step = _clear_step(adv, info)
        ctx = approval_repo.load_context(db)
        if clr.can_approve(info, ctx.people, ctx.mappings, sub.created_by, employee_id, step,
                           _clear_round_approvers(db, adv.form_id)):
            out.append({"form_id": adv.form_id, "amount_actual": logic.jsonable(adv.amount_actual),
                        "amount_paid": logic.jsonable(adv.amount_paid),
                        "settle_amount": logic.jsonable(adv.settle_amount),
                        "step": step, "total_steps": len(info["steps"]),
                        "clause": info["clause"], "requester_employee_id": sub.created_by,
                        "clear_submitted_at": repo._iso(adv.clear_submitted_at)})
    # ชื่อ/แผนกผู้เบิก ให้การ์ดในแท็บรออนุมัติเคลียร์อ่านง่าย
    people = repo.people_by_employee_id(db, [o["requester_employee_id"] for o in out]) if out else {}
    for o in out:
        person = people.get(o["requester_employee_id"]) or {}
        o["requester_name"], o["requester_department"] = person.get("name"), person.get("department")
    return out


@router.put("/advances/{form_id}/clear-approve")
def clear_approve(form_id: str, body: ClearApproveIn, background_tasks: BackgroundTasks,
                  db: Session = Depends(get_db)):
    sub, adv, status = _load_for_update(db, form_id)
    pending = status == logic.CLEARING_APPROVAL
    try:
        if adv is None or not pending:
            raise logic.InvalidTransition(clr.NOT_PENDING)
        info, ctx = _clear_plan(db, sub)
        step = _clear_step(adv, info)
        eligible = clr.can_approve(info, ctx.people, ctx.mappings, sub.created_by, body.action_by,
                                   step, _clear_round_approvers(db, form_id))
        clr.check_approve(pending, eligible)
    except logic.AdvanceRuleError as exc:
        raise _rule_error(exc)

    # ข้ามขั้นแบบเดียวกับตอนขอเบิก: ระดับของคนกดถึงขั้นถัดไปแล้ว ผ่านขั้นนั้นไปด้วยเลย
    # superadmin กดแทนได้ทุกขั้นแต่ไม่ข้ามขั้น (เหมือน approval_logic.decide) — คิดก่อนบันทึกการกดครั้งนี้
    level, who = _clear_approver_level(info, ctx, sub.created_by, body.action_by, step,
                                       _clear_round_approvers(db, form_id))
    _clear_log(db, form_id, step, "APPROVED", info["clause"], adv.amount_actual,
               body.remark, body.action_by)
    done = _clear_round_steps(db, form_id)
    for skipped in approval_rules.skipped_steps(info["steps"], step, level, who, done):
        spec = clr.step_spec(info, skipped)
        _clear_log(db, form_id, skipped, "APPROVED", info["clause"], adv.amount_actual,
                   approval_rules.skip_note(spec, step, level), body.action_by)
    _outcome, nxt = approval_rules.next_after_approval(info["steps"], step, level, who, done)
    if nxt is None:
        adv.fin_status = logic.FIN_CLEARING_SUBMITTED
        adv.clear_approval_level = None
        adv.clear_approved_clause = info["clause"]
    else:
        adv.clear_approval_level = nxt
    amounts = {"amount_paid": adv.amount_paid, "amount_actual": adv.amount_actual,
               "settle_amount": adv.settle_amount}
    detail = _commit_and_return(db, form_id)
    if nxt is not None:  # ผ่านขั้น 1 แล้ว แจ้งผู้อนุมัติขั้นถัดไป
        _queue_clear_pending(background_tasks, db, sub, info, ctx, nxt, **amounts)
    if nxt is None:  # ถึงมือบัญชีแล้ว: บอกผู้เบิกให้พิมพ์เอกสารส่งตัวจริง CC บัญชี (ผู้ใช้สั่ง 20/10/2026)
        approver = repo.people_by_employee_id(db, [body.action_by]).get(body.action_by) or {}
        mail.queue_event(background_tasks, db, mail.CLEAR_APPROVED, sub,
                         approver_name=approver.get("name"), clear_due_date=adv.clear_due_date, **amounts)
    return detail


def _clear_approver_level(info, ctx, requester_id, approver_id, step, already):
    """(ระดับ, ตัวตน) ที่ใช้ตัดสินการข้ามขั้น — superadmin ที่กดได้เพราะสิทธิ์พิเศษนับเป็นระดับของขั้นนี้พอดี
    และไม่นับตัวตน (ไม่ข้ามขั้นพิเศษ)"""
    approver = ctx.people.get(approver_id)
    requester = ctx.people.get(requester_id) or {}
    spec = clr.step_spec(info, step)
    if approver is not None and approval_rules.may_approve(ctx.people.values(), approver, requester,
                                                           spec["required_level"], ctx.mappings, already,
                                                           spec.get("approver")):
        return approver.get("level"), approver_id
    return spec["required_level"], None


@router.put("/advances/{form_id}/clear-reject")
def clear_reject(form_id: str, body: ClearRejectIn, background_tasks: BackgroundTasks,
                 db: Session = Depends(get_db)):
    sub, adv, status = _load_for_update(db, form_id)
    pending = status == logic.CLEARING_APPROVAL
    try:
        if adv is None or not pending:
            raise logic.InvalidTransition(clr.NOT_PENDING)
        info, ctx = _clear_plan(db, sub)
        step = _clear_step(adv, info)
        eligible = clr.can_approve(info, ctx.people, ctx.mappings, sub.created_by, body.action_by,
                                   step, _clear_round_approvers(db, form_id))
        remark = clr.check_reject(pending, eligible, body.review_remark)
    except logic.AdvanceRuleError as exc:
        raise _rule_error(exc)

    _clear_log(db, form_id, step, "REJECTED", info["clause"], adv.amount_actual, remark, body.action_by)
    adv.fin_status = logic.FIN_SENT_BACK
    adv.clear_approval_level = None
    adv.review_remark = remark
    db.add(FinAdvanceLog(advance_id=adv.id, action="CLEAR_REJECT", remark=remark, action_by=body.action_by))
    detail = _commit_and_return(db, form_id)
    # ผู้เบิกต้องรู้ว่าหัวหน้าไม่อนุมัติการเคลียร์และต้องแก้อะไร (รอบทดสอบทั้ง flow 20/10/2026)
    rejecter = repo.people_by_employee_id(db, [body.action_by]).get(body.action_by) or {}
    mail.queue_event(background_tasks, db, mail.CLEAR_REJECTED, sub, remark=remark,
                     approver_name=rejecter.get("name"))
    return detail


@router.put("/advances/{form_id}/send-back")
def send_back_advance(form_id: str, body: SendBackIn, background_tasks: BackgroundTasks,
                      db: Session = Depends(get_db)):
    require_permission(db, body.action_by, perms.PERM_CLEARING_REVIEW)
    sub, adv, status = _load_for_update(db, form_id)
    try:
        logic.check_fresh(body.expected_clear_submitted_at, adv.clear_submitted_at if adv is not None else None)
        logic.check_send_back(status, review_remark=body.review_remark)
    except logic.AdvanceRuleError as exc:
        raise _rule_error(exc)
    adv.fin_status = logic.FIN_SENT_BACK
    adv.review_remark = body.review_remark
    db.add(FinAdvanceLog(advance_id=adv.id, action="SEND_BACK", remark=body.review_remark,
                         action_by=body.action_by))
    detail = _commit_and_return(db, form_id)
    mail.queue_event(background_tasks, db, mail.SENT_BACK, sub, remark=body.review_remark)
    return detail


@router.put("/advances/{form_id}/confirm")
def confirm_advance(form_id: str, body: ConfirmIn, background_tasks: BackgroundTasks,
                    db: Session = Depends(get_db)):
    require_permission(db, body.action_by, perms.PERM_CLOSE)
    sub, adv, status = _load_for_update(db, form_id)
    try:
        logic.check_fresh(body.expected_clear_submitted_at, adv.clear_submitted_at if adv is not None else None)
        extra_paid_on = logic.check_confirm(status, settle_amount=adv.settle_amount if adv is not None else None,
                                            settle_date=body.settle_date)
        clear_doc_no = logic.check_clear_doc_no(body.clear_doc_no)
        received_on = logic.check_original_received(body.original_received_date, repo.today_bkk())
    except logic.AdvanceRuleError as exc:
        raise _rule_error(exc)
    changes = {}
    if extra_paid_on is not None:
        changes = logic.diff_fields({"settle_date": adv.settle_date}, {"settle_date": extra_paid_on})
        adv.settle_date = extra_paid_on
    # เอกสารเคลียร์ + วันที่รับเอกสารตัวจริง บันทึกโดยบัญชีตอนปิดรายการ
    changes = {**changes, **logic.diff_fields(
        {"clear_doc_no": adv.clear_doc_no, "original_received_date": adv.original_received_date},
        {"clear_doc_no": clear_doc_no, "original_received_date": received_on})}
    adv.clear_doc_no = clear_doc_no
    adv.original_received_date = received_on
    adv.fin_status = logic.FIN_CLOSED
    adv.closed_by = body.action_by
    adv.closed_at = func.now()
    db.add(FinAdvanceLog(advance_id=adv.id, action="CONFIRM", changes=changes or None, action_by=body.action_by))
    detail = _commit_and_return(db, form_id)
    mail.queue_event(background_tasks, db, mail.CLOSED, sub)
    return detail
