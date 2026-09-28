"""Finance — เบิกเงิน Advance.

ยังไม่มี auth เหมือน route อื่นของ api-ncac ในตอนนี้ (ใช้ทดสอบ local เท่านั้น) — ต้องทำ launch gate
ด้านความปลอดภัยก่อนขึ้น production ดู spec §9 ใน menait-service
"""
import os
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models.finance_model import FinAccount
from models.user_model import User
from schemas.finance_schema import AccountCreate, AccountUpdate
from services.finance import advance_logic as logic
from services.finance import advance_repo as repo

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
    db.commit()
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


@router.get("/advances/{form_id}")
def get_advance(form_id: str, db: Session = Depends(get_db)):
    detail = repo.get_advance_detail(db, form_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ไม่พบรายการเบิกเงิน")
    return detail
