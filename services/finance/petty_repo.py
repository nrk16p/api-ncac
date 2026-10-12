"""ทุก query/commit ของเบิกเงินสดย่อย — กฎอยู่ที่ petty_logic/petty_validate ไม่ใช่ที่นี่"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from models.finance_petty_model import FinPettyExpenseType, FinPettyFund


def list_expense_types(db: Session, active_only: bool = True) -> list[FinPettyExpenseType]:
    q = db.query(FinPettyExpenseType)
    if active_only:
        q = q.filter(FinPettyExpenseType.is_active.is_(True))
    return q.order_by(FinPettyExpenseType.sort_order, FinPettyExpenseType.code).all()


def get_expense_type(db: Session, code: str) -> Optional[FinPettyExpenseType]:
    return db.query(FinPettyExpenseType).filter(FinPettyExpenseType.code == code).first()


def upsert_expense_type(db: Session, code: str, payload: dict, actor: str) -> FinPettyExpenseType:
    row = get_expense_type(db, code)
    if row is None:
        row = FinPettyExpenseType(code=code)
        db.add(row)
    for key, value in payload.items():
        setattr(row, key, value)
    row.updated_by = actor
    db.commit()
    db.refresh(row)
    return row


def list_funds(db: Session, holder: Optional[str] = None, active_only: bool = True) -> list[FinPettyFund]:
    q = db.query(FinPettyFund)
    if active_only:
        q = q.filter(FinPettyFund.is_active.is_(True))
    if holder:
        q = q.filter(FinPettyFund.holder_employee_id == holder)
    return q.order_by(FinPettyFund.branch_code, FinPettyFund.division_code).all()


def get_fund(db: Session, fund_id: int) -> Optional[FinPettyFund]:
    return db.query(FinPettyFund).filter(FinPettyFund.id == fund_id).first()


def create_fund(db: Session, payload: dict, actor: str) -> FinPettyFund:
    row = FinPettyFund(**payload, updated_by=actor)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_fund(db: Session, fund_id: int, payload: dict, actor: str) -> FinPettyFund:
    row = get_fund(db, fund_id)
    if row is None:
        raise LookupError("ไม่พบกองทุน")
    for key, value in payload.items():
        setattr(row, key, value)
    row.updated_by = actor
    db.commit()
    db.refresh(row)
    return row
