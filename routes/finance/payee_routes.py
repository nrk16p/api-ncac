"""Finance — payee account master (บัญชีรับเงินพนักงาน) and change requests.

ยังไม่มี auth เหมือน route อื่นของ api-ncac — FE proxy เป็นด่านเดียว (launch gate เดิม) ส่วนที่เป็นของฝ่ายการเงิน
ตรวจ action_by ด้วย require_finance เหมือน advance_routes
"""
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from models.finance_model import FinPayeeAccount, FinPayeeAccountLog, FinPayeeAccountRequest
from models.user_model import User
from routes.finance.advance_routes import require_finance
from schemas.finance_schema import (PayeeAccountCreate, PayeeAccountUpdate, PayeeActionIn, PayeeRejectIn,
                                    PayeeRequestCreate)
from services.email_service import send_email
from services.finance import advance_logic as logic
from services.finance import payee_logic as pl
from services.finance import payee_repo as repo
from services.finance.advance_repo import people_by_employee_id
from services.finance.payee_email import render_payee_request_email

router = APIRouter(prefix="/finance", tags=["Finance - Payee"])
log = logging.getLogger(__name__)

_NOT_FOUND_EMP = "ไม่พบรหัสพนักงาน"
_HAS_MASTER = "พนักงานนี้มีบัญชีใน Master แล้ว — กรุณาแก้ไขแทน"
_HAS_PENDING = "มีคำขอที่รอบัญชีตรวจสอบอยู่แล้ว"
_NOT_OWNER = "เฉพาะผู้ขอเท่านั้นที่ยกเลิกคำขอได้"


def _rule_error(exc: logic.AdvanceRuleError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=str(exc))


def _now():
    return datetime.now(timezone.utc)


def _employee_exists(db: Session, employee_id: str) -> bool:
    return db.query(User.employee_id).filter(User.employee_id == employee_id).first() is not None


def _commit(db: Session, conflict: str):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=conflict)


def _load_request(db: Session, request_id: int, for_update=False) -> FinPayeeAccountRequest:
    query = db.query(FinPayeeAccountRequest).filter(FinPayeeAccountRequest.id == request_id)
    if for_update:
        query = query.with_for_update()
    row = query.first()
    if row is None:
        raise HTTPException(status_code=404, detail="ไม่พบคำขอ")
    return row


def _account_values(row) -> dict:
    if row is None:
        return {"account_no": None, "account_name": None, "status": None}
    return {"account_no": row.account_no, "account_name": row.account_name, "status": row.status}


def _log(db: Session, employee_id, action, changes=None, remark=None, action_by=None):
    db.add(FinPayeeAccountLog(employee_id=employee_id, action=action, changes=changes, remark=remark,
                              action_by=action_by))


# ---------------------------- people lookup ----------------------------

@router.get("/people/{employee_id}")
def get_person(employee_id: str, db: Session = Depends(get_db)):
    person = people_by_employee_id(db, [employee_id]).get(employee_id)
    if person is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND_EMP)
    return {k: person.get(k) for k in ("employee_id", "name", "department", "position")}


# ---------------------------- master ----------------------------

@router.get("/payee-accounts/me")
def my_payee_account(employee_id: str, db: Session = Depends(get_db)):
    account = repo.get_master(db, employee_id)
    request = (db.query(FinPayeeAccountRequest).filter(FinPayeeAccountRequest.employee_id == employee_id)
               .order_by(FinPayeeAccountRequest.created_at.desc(), FinPayeeAccountRequest.id.desc()).first())
    return {"account": repo.account_dict(db, account) if account else None,
            "request": repo.request_dict(db, request) if request else None}


@router.get("/payee-accounts")
def list_payee_accounts(q: Optional[str] = None, status: Optional[str] = None, db: Session = Depends(get_db)):
    return repo.list_accounts(db, q=q, status=status)


@router.post("/payee-accounts", status_code=201)
def create_payee_account(body: PayeeAccountCreate, db: Session = Depends(get_db)):
    require_finance(db, body.action_by)
    try:
        account_no = pl.check_kbank_account(body.account_no)
        account_name = pl.check_account_name(body.account_name)
    except logic.AdvanceRuleError as exc:
        raise _rule_error(exc)
    if not _employee_exists(db, body.employee_id):
        raise HTTPException(status_code=404, detail=_NOT_FOUND_EMP)
    if repo.get_master(db, body.employee_id) is not None:
        raise HTTPException(status_code=409, detail=_HAS_MASTER)
    row = FinPayeeAccount(employee_id=body.employee_id, bank=pl.PAYEE_BANK, account_no=account_no,
                          account_name=account_name, status="ACTIVE", created_by=body.action_by,
                          updated_by=body.action_by, created_at=_now(), updated_at=_now())
    db.add(row)
    _log(db, body.employee_id, "CREATE", pl.diff_pairs(_account_values(None), _account_values(row)),
         action_by=body.action_by)
    _commit(db, _HAS_MASTER)
    return repo.account_dict(db, row)


@router.put("/payee-accounts/{account_id}")
def update_payee_account(account_id: int, body: PayeeAccountUpdate, db: Session = Depends(get_db)):
    require_finance(db, body.action_by)
    row = (db.query(FinPayeeAccount).filter(FinPayeeAccount.id == account_id).with_for_update().first())
    if row is None:
        raise HTTPException(status_code=404, detail="ไม่พบบัญชี")
    before = _account_values(row)
    try:
        if body.account_no is not None:
            row.account_no = pl.check_kbank_account(body.account_no)
        if body.account_name is not None:
            row.account_name = pl.check_account_name(body.account_name)
        if body.status is not None:
            row.status = pl.check_master_status(body.status)
    except logic.AdvanceRuleError as exc:
        raise _rule_error(exc)
    changes = pl.diff_pairs(before, _account_values(row))
    if changes:
        if set(changes) == {"status"}:
            action = "DEACTIVATE" if row.status == "INACTIVE" else "REACTIVATE"
        else:
            action = "UPDATE"
        row.updated_by = body.action_by
        row.updated_at = _now()
        _log(db, row.employee_id, action, changes, action_by=body.action_by)
    db.commit()
    return repo.account_dict(db, row)


@router.get("/payee-accounts/{account_id}/logs")
def payee_account_logs(account_id: int, db: Session = Depends(get_db)):
    row = db.get(FinPayeeAccount, account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="ไม่พบบัญชี")
    return repo.list_logs(db, row.employee_id)


# ---------------------------- requests ----------------------------

def _send_request_email(ctx: dict):
    """Background task: must never raise."""
    try:
        subject, html = render_payee_request_email(ctx)
        to = os.getenv("FINANCE_ACCOUNT_EMAIL") or "accountbkk@menatransport.co.th"
        send_email(to, subject, html)
    except Exception:  # noqa: BLE001 - email failure must not affect the saved request
        log.exception("payee request email failed (request %s)", ctx.get("request_id"))


@router.post("/payee-requests", status_code=201)
def create_payee_request(body: PayeeRequestCreate, background_tasks: BackgroundTasks,
                         db: Session = Depends(get_db)):
    try:
        account_no = pl.check_kbank_account(body.account_no)
        account_name = pl.check_account_name(body.account_name)
    except logic.AdvanceRuleError as exc:
        raise _rule_error(exc)
    person = people_by_employee_id(db, [body.employee_id]).get(body.employee_id)
    if person is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND_EMP)
    pending = (db.query(FinPayeeAccountRequest.id)
               .filter(FinPayeeAccountRequest.employee_id == body.employee_id,
                       FinPayeeAccountRequest.status == "PENDING").first())
    if pending is not None:
        raise HTTPException(status_code=409, detail=_HAS_PENDING)
    row = FinPayeeAccountRequest(employee_id=body.employee_id, bank=pl.PAYEE_BANK, account_no=account_no,
                                 account_name=account_name, remark=body.remark, status="PENDING",
                                 created_at=_now())
    db.add(row)
    try:
        db.flush()  # id for the log/email; the partial unique index fires here on a double click
        _log(db, body.employee_id, "REQUEST",
             {**pl.diff_pairs({k: v for k, v in _account_values(repo.get_master(db, body.employee_id)).items()
                              if k != "status"},
                              {"account_no": account_no, "account_name": account_name}),
              "request_status": [None, "PENDING"]},
             remark=body.remark, action_by=body.employee_id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=_HAS_PENDING)
    background_tasks.add_task(_send_request_email, {
        "request_id": row.id, "employee_id": row.employee_id, "employee_name": person.get("name"),
        "department": person.get("department"), "position": person.get("position"),
        "account_no": row.account_no, "account_name": row.account_name, "remark": row.remark,
        "app_origin": body.app_origin,
    })
    return repo.request_dict(db, row)


@router.get("/payee-requests")
def list_payee_requests(status: str = "PENDING", db: Session = Depends(get_db)):
    return repo.list_requests(db, status=status)


@router.get("/payee-requests/{request_id}")
def get_payee_request(request_id: int, db: Session = Depends(get_db)):
    return repo.request_dict(db, _load_request(db, request_id))


@router.put("/payee-requests/{request_id}/approve")
def approve_payee_request(request_id: int, body: PayeeActionIn, db: Session = Depends(get_db)):
    require_finance(db, body.action_by)
    req = _load_request(db, request_id, for_update=True)
    try:
        pl.check_request_open(req.status)
    except logic.AdvanceRuleError as exc:
        raise _rule_error(exc)
    master = repo.get_master(db, req.employee_id, for_update=True)
    before = _account_values(master)
    now = _now()
    if master is None:
        master = FinPayeeAccount(employee_id=req.employee_id, bank=pl.PAYEE_BANK, created_by=body.action_by,
                                 created_at=now)
        db.add(master)
    master.account_no = req.account_no
    master.account_name = req.account_name
    master.status = "ACTIVE"
    master.source_request_id = req.id
    master.updated_by = body.action_by
    master.updated_at = now
    req.status = "APPROVED"
    req.reviewed_by = body.action_by
    req.reviewed_at = now
    _log(db, req.employee_id, "APPROVE", pl.diff_pairs(before, _account_values(master)),
         remark=req.remark, action_by=body.action_by)
    _commit(db, "ไม่สามารถอนุมัติได้ — มีรายการซ้ำ กรุณาลองใหม่")
    return {"request": repo.request_dict(db, req), "account": repo.account_dict(db, master)}


@router.put("/payee-requests/{request_id}/reject")
def reject_payee_request(request_id: int, body: PayeeRejectIn, db: Session = Depends(get_db)):
    require_finance(db, body.action_by)
    req = _load_request(db, request_id, for_update=True)
    try:
        pl.check_request_open(req.status)
        remark = pl.check_reject_remark(body.review_remark)
    except logic.AdvanceRuleError as exc:
        raise _rule_error(exc)
    req.status = "REJECTED"
    req.review_remark = remark
    req.reviewed_by = body.action_by
    req.reviewed_at = _now()
    _log(db, req.employee_id, "REQUEST_REJECT", {"status": ["PENDING", "REJECTED"]}, remark=remark,
         action_by=body.action_by)
    db.commit()
    return repo.request_dict(db, req)


@router.put("/payee-requests/{request_id}/cancel")
def cancel_payee_request(request_id: int, body: PayeeActionIn, db: Session = Depends(get_db)):
    req = _load_request(db, request_id, for_update=True)
    if body.action_by != req.employee_id:
        raise HTTPException(status_code=403, detail=_NOT_OWNER)
    try:
        pl.check_request_open(req.status)
    except logic.AdvanceRuleError as exc:
        raise _rule_error(exc)
    req.status = "CANCELLED"
    _log(db, req.employee_id, "REQUEST_CANCEL", {"status": ["PENDING", "CANCELLED"]}, action_by=body.action_by)
    db.commit()
    return repo.request_dict(db, req)
