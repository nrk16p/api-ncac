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


# ---------------------------- claims ----------------------------

from schemas.finance_petty_schema import ClaimIn  # noqa: E402


def _item_dict(i) -> dict:
    return {"id": i.id, "line_no": i.line_no, "event_date": str(i.event_date),
            "truck_code": i.truck_code, "truck_plate": i.truck_plate, "truck_type": i.truck_type,
            "ref_no": i.ref_no, "qty": float(i.qty), "rate": float(i.rate),
            "amount": float(i.amount), "remark": i.remark}


def _claim_dict(c, items) -> dict:
    return {
        "id": c.id, "form_id": c.form_id, "fund_id": c.fund_id, "expense_type": c.expense_type,
        "expense_date": str(c.expense_date),
        "period_from": str(c.period_from) if c.period_from else None,
        "period_to": str(c.period_to) if c.period_to else None,
        "driver_employee_id": c.driver_employee_id, "driver_name": c.driver_name,
        "payee_name": c.payee_name, "amount": float(c.amount), "dd_no": c.dd_no,
        "receipt_no": c.receipt_no, "status": c.status, "created_by": c.created_by,
        "approved_by": c.approved_by, "approved_at": c.approved_at.isoformat() if c.approved_at else None,
        "transfer_date": str(c.transfer_date) if c.transfer_date else None,
        "review_remark": c.review_remark, "return_reason": c.return_reason,
        "dup_override_reason": c.dup_override_reason, "batch_id": c.batch_id,
        "items": [_item_dict(i) for i in items],
    }


@router.post("/claims", status_code=201)
def create_claim(payload: ClaimIn, db: Session = Depends(get_db)):
    body = payload.model_dump()
    items = body.pop("items")
    try:
        claim = repo.create_claim(db, body, items, actor=payload.created_by)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _claim_dict(claim, repo.claim_items(db, claim.id))


@router.get("/claims")
def list_claims(employee_id: Optional[str] = None, holder: Optional[str] = None,
                status: Optional[str] = None, month: Optional[str] = None,
                limit: int = Query(default=50, le=200), offset: int = 0,
                db: Session = Depends(get_db)):
    """employee_id = ใบที่คนนั้นเปิด · holder = ทุกใบของกองทุนที่คนนั้นถือ · ไม่ส่งทั้งคู่ = ทุกใบ (บัญชี/การเงิน)"""
    fund_ids = [f.id for f in repo.list_funds(db, holder=holder)] if holder else None
    rows, total = repo.list_claims(db, fund_ids=fund_ids, created_by=employee_id, status=status,
                                   month=month, limit=limit, offset=offset)
    return {"total": total, "items": [_claim_dict(c, repo.claim_items(db, c.id)) for c in rows]}


@router.get("/claims/{form_id}")
def get_claim(form_id: str, db: Session = Depends(get_db)):
    claim = repo.get_claim(db, form_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="ไม่พบใบเบิก")
    return _claim_dict(claim, repo.claim_items(db, claim.id))


# ---------------------------- transitions ----------------------------

from datetime import datetime, timezone  # noqa: E402

from schemas.finance_petty_schema import ApproveIn, ReturnIn, ReviewIn, SubmitAccIn, TransferIn  # noqa: E402
from services.finance import petty_logic as L  # noqa: E402


def _claim_or_404(db: Session, form_id: str):
    claim = repo.get_claim(db, form_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="ไม่พบใบเบิก")
    return claim


def _require_holder(db: Session, claim, employee_id: str):
    fund = repo.get_fund(db, claim.fund_id)
    if fund is None or fund.holder_employee_id != employee_id:
        raise HTTPException(status_code=403, detail="เฉพาะผู้อนุมัติของกองทุนนี้เท่านั้น")
    return fund


def _rule_error(exc: L.PettyRuleError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=str(exc))


@router.put("/claims/{form_id}")
def update_claim(form_id: str, payload: ClaimIn, db: Session = Depends(get_db)):
    claim = _claim_or_404(db, form_id)
    if claim.created_by != payload.created_by:
        raise HTTPException(status_code=403, detail="แก้ได้เฉพาะใบที่ตัวเองเปิด")
    body = payload.model_dump(exclude={"created_by", "fund_id", "expense_type"})
    items = body.pop("items")
    try:
        out = repo.update_draft(db, claim, body, items, actor=payload.created_by)
    except L.PettyRuleError as exc:
        raise _rule_error(exc)
    return _claim_dict(out, repo.claim_items(db, out.id))


@router.post("/claims/{form_id}/approve")
def approve_claim(form_id: str, payload: ApproveIn, db: Session = Depends(get_db)):
    claim = _claim_or_404(db, form_id)
    _require_holder(db, claim, payload.employee_id)
    fields = {"approved_by": payload.employee_id}
    if payload.dup_override_reason:
        fields["dup_override_reason"] = payload.dup_override_reason
    try:
        out = repo.set_status(db, claim, L.APPROVED, payload.employee_id, "APPROVE", fields=fields)
    except L.PettyRuleError as exc:
        raise _rule_error(exc)
    return _claim_dict(out, repo.claim_items(db, out.id))


@router.post("/claims/{form_id}/return")
def return_claim(form_id: str, payload: ReturnIn, db: Session = Depends(get_db)):
    claim = _claim_or_404(db, form_id)
    _require_holder(db, claim, payload.employee_id)
    try:
        out = repo.set_status(db, claim, L.RETURNED, payload.employee_id, "RETURN",
                              fields={"return_reason": payload.reason}, remark=payload.reason)
    except L.PettyRuleError as exc:
        raise _rule_error(exc)
    return _claim_dict(out, repo.claim_items(db, out.id))


@router.post("/claims/{form_id}/transfer")
def transfer_claim(form_id: str, payload: TransferIn, db: Session = Depends(get_db)):
    claim = _claim_or_404(db, form_id)
    _require_holder(db, claim, payload.employee_id)
    try:
        out = repo.set_status(db, claim, L.TRANSFERRED, payload.employee_id, "TRANSFER",
                              fields={"transfer_date": payload.transfer_date})
    except L.PettyRuleError as exc:
        raise _rule_error(exc)
    return _claim_dict(out, repo.claim_items(db, out.id))


@router.post("/claims/{form_id}/submit-accounting")
def submit_to_accounting(form_id: str, payload: SubmitAccIn, db: Session = Depends(get_db)):
    claim = _claim_or_404(db, form_id)
    _require_holder(db, claim, payload.employee_id)
    fields = {"dd_no": payload.dd_no} if payload.dd_no else {}
    try:
        out = repo.set_status(db, claim, L.AWAITING_REVIEW, payload.employee_id, "SUBMIT_ACC",
                              fields=fields)
    except L.PettyRuleError as exc:
        raise _rule_error(exc)
    return _claim_dict(out, repo.claim_items(db, out.id))


@router.post("/claims/{form_id}/review")
def review_claim(form_id: str, payload: ReviewIn, db: Session = Depends(get_db)):
    """บัญชีตรวจ — ผ่านแล้วรอการเงินปิดรอบ (ยังไม่ CLOSED) หรือส่งกลับให้ผู้อนุมัติแก้"""
    claim = _claim_or_404(db, form_id)
    if payload.action == "SEND_BACK":
        if not payload.remark:
            raise HTTPException(status_code=422, detail="ส่งกลับต้องระบุเหตุผล")
        try:
            out = repo.set_status(db, claim, L.SENT_BACK, payload.employee_id, "SEND_BACK",
                                  fields={"review_remark": payload.remark}, remark=payload.remark)
        except L.PettyRuleError as exc:
            raise _rule_error(exc)
        return _claim_dict(out, repo.claim_items(db, out.id))
    if claim.status != L.AWAITING_REVIEW:
        raise HTTPException(status_code=409,
                            detail=f"ตรวจได้เฉพาะใบที่รอบัญชีตรวจ ใบนี้สถานะ "
                                   f"{L.STATUS_LABELS.get(claim.status, claim.status)}")
    # PASS: ทำเครื่องหมายว่าบัญชีตรวจแล้ว ใบยังอยู่ AWAITING_REVIEW จนการเงินปิดรอบ
    claim.reviewed_by, claim.review_remark = payload.employee_id, payload.remark
    claim.reviewed_at = datetime.now(timezone.utc)
    repo.log(db, claim.id, "ACC_PASS", payload.employee_id, remark=payload.remark)
    db.commit()
    db.refresh(claim)
    return _claim_dict(claim, repo.claim_items(db, claim.id))
