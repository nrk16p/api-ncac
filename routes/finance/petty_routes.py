"""Finance — เบิกเงินสดย่อย.

auth ยังเป็นแบบเดียวกับ advance_routes (ไม่มี auth ที่ชั้นนี้ FE เป็นคนการ์ด) — ดู launch gate ใน spec
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from schemas.finance_petty_schema import ExpenseTypeUpdate, FundCreate, FundUpdate
from services.finance import petty_repo as repo

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/finance/petty", tags=["Finance - Petty Cash"])


def _type_dict(t) -> dict:
    return {"code": t.code, "name_th": t.name_th, "atms_sku": t.atms_sku,
            "sort_order": t.sort_order, "is_active": t.is_active, "spec": t.spec}


def _fund_dict(f) -> dict:
    return {"id": f.id, "branch_code": f.branch_code, "division_code": f.division_code,
            "holder_employee_id": f.holder_employee_id,
            "fund_limit": float(f.fund_limit) if f.fund_limit is not None else None,
            "is_active": f.is_active}


@router.get("/expense-types")
def list_expense_types(active: bool = True, db: Session = Depends(get_db)):
    return [_type_dict(t) for t in repo.list_expense_types(db, active_only=active)]


@router.put("/expense-types/{code}")
def put_expense_type(code: str, payload: ExpenseTypeUpdate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude={"updated_by"})
    row = repo.upsert_expense_type(db, code, data, payload.updated_by or "")
    return _type_dict(row)


@router.get("/funds")
def list_funds(holder: Optional[str] = Query(default=None), active: bool = True,
               db: Session = Depends(get_db)):
    return [_fund_dict(f) for f in repo.list_funds(db, holder=holder, active_only=active)]


@router.post("/funds", status_code=201)
def create_fund(payload: FundCreate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude={"updated_by"})
    try:
        return _fund_dict(repo.create_fund(db, data, payload.updated_by or ""))
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="สาขาและแผนกนี้มีกองทุนอยู่แล้ว")


@router.put("/funds/{fund_id}")
def update_fund(fund_id: int, payload: FundUpdate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude_unset=True, exclude={"updated_by"})
    try:
        return _fund_dict(repo.update_fund(db, fund_id, data, payload.updated_by or ""))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
