"""Finance — เบิกเงินสดย่อย.

auth ยังเป็นแบบเดียวกับ advance_routes (ไม่มี auth ที่ชั้นนี้ FE เป็นคนการ์ด) — ดู launch gate ใน spec
"""
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from schemas.finance_petty_schema import ExpenseTypeUpdate, FundCreate, FundUpdate
from services.finance import petty_logic as L
from services.finance import petty_repo as repo

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/finance/petty", tags=["Finance - Petty Cash"])


def _type_dict(t) -> dict:
    return {"code": t.code, "name_th": t.name_th, "atms_sku": t.atms_sku,
            "sort_order": t.sort_order, "is_active": t.is_active, "spec": t.spec}


def _fund_dict(f, names: dict | None = None) -> dict:
    return {"id": f.id, "branch_code": f.branch_code, "division_code": f.division_code,
            "holder_employee_id": f.holder_employee_id,
            "holder_name": (names or {}).get(f.holder_employee_id),
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
    rows = repo.list_funds(db, holder=holder, active_only=active)
    names = repo.employee_names(db, [f.holder_employee_id for f in rows])
    return [_fund_dict(f, names) for f in rows]


@router.post("/funds", status_code=201)
def create_fund(payload: FundCreate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude={"updated_by"})
    try:
        fund = repo.create_fund(db, data, payload.updated_by or "")
        return _fund_dict(fund, repo.employee_names(db, [fund.holder_employee_id]))
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="สาขาและแผนกนี้มีกองทุนอยู่แล้ว")


@router.put("/funds/{fund_id}")
def update_fund(fund_id: int, payload: FundUpdate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude_unset=True, exclude={"updated_by"})
    try:
        fund = repo.update_fund(db, fund_id, data, payload.updated_by or "")
        return _fund_dict(fund, repo.employee_names(db, [fund.holder_employee_id]))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ---------------------------- claims ----------------------------

from schemas.finance_petty_schema import ClaimIn  # noqa: E402


def _item_dict(i) -> dict:
    return {"id": i.id, "line_no": i.line_no, "event_date": str(i.event_date),
            "truck_code": i.truck_code, "truck_plate": i.truck_plate, "truck_type": i.truck_type,
            "ref_no": i.ref_no, "qty": float(i.qty), "rate": float(i.rate),
            "amount": float(i.amount), "remark": i.remark, "driver_name": i.driver_name}


def _claim_dict(c, items, names: dict | None = None) -> dict:
    names = names or {}
    return {
        "id": c.id, "form_id": c.form_id, "fund_id": c.fund_id, "expense_type": c.expense_type,
        "expense_date": str(c.expense_date),
        "period_from": str(c.period_from) if c.period_from else None,
        "period_to": str(c.period_to) if c.period_to else None,
        "driver_employee_id": c.driver_employee_id, "driver_name": c.driver_name,
        "payee_name": c.payee_name, "amount": float(c.amount), "dd_no": c.dd_no,
        "receipt_no": c.receipt_no, "detail": c.detail or {}, "status": c.status, "created_by": c.created_by,
        "created_by_name": names.get(c.created_by), "approved_by_name": names.get(c.approved_by),
        "approved_by": c.approved_by, "approved_at": c.approved_at.isoformat() if c.approved_at else None,
        "transfer_date": str(c.transfer_date) if c.transfer_date else None,
        "review_remark": c.review_remark, "return_reason": c.return_reason,
        "reviewed_by": c.reviewed_by,
        "reviewed_at": c.reviewed_at.isoformat() if c.reviewed_at else None,
        "submitted_to_acc_at": c.submitted_to_acc_at.isoformat() if c.submitted_to_acc_at else None,
        "dup_override_reason": c.dup_override_reason, "batch_id": c.batch_id,
        "items": [_item_dict(i) for i in items],
    }


def _reject_incomplete_rows(db: Session, expense_type: str, items: list[dict]) -> None:
    """ช่องที่ spec บังคับ (เลขที่บัตรชั่ง รถ ฯลฯ) ต้องครบตั้งแต่บันทึก ไม่ปล่อยให้ค้างถึงด่านอนุมัติ"""
    type_row = repo.get_expense_type(db, expense_type)
    if type_row is None:
        raise HTTPException(status_code=404, detail="ไม่พบประเภทการเบิกนี้ หรือถูกปิดใช้งานแล้ว")
    spec = type_row.spec or {}
    missing = validate.missing_item_fields(items, spec) + validate.future_dated_rows(items, L.bkk_today())
    if missing:
        raise HTTPException(status_code=422, detail={"error": "กรอกรายการไม่ครบ", "missing": missing})


@router.post("/claims", status_code=201)
def create_claim(payload: ClaimIn, db: Session = Depends(get_db)):
    body = payload.model_dump()
    items = body.pop("items")
    _reject_incomplete_rows(db, payload.expense_type, items)
    try:
        claim = repo.create_claim(db, body, items, actor=payload.created_by)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _out(db, claim)


def _out(db: Session, claim) -> dict:
    names = repo.employee_names(db, [claim.created_by, claim.approved_by])
    items = repo.claim_items(db, claim.id)
    body = _claim_dict(claim, items, names)
    seq = repo.month_sequence(db, claim, items)  # ใบที่เท่าไรของรถคันนั้นในเดือน (เฉพาะรายละเอียดใบ)
    for row in body["items"]:
        row["month_seq"] = seq.get(row["id"])
    return body


@router.get("/claims")
def list_claims(employee_id: Optional[str] = None, holder: Optional[str] = None,
                status: Optional[str] = None, month: Optional[str] = None,
                limit: int = Query(default=50, le=200), offset: int = 0,
                db: Session = Depends(get_db)):
    """employee_id = ใบที่คนนั้นเปิด · holder = ทุกใบของกองทุนที่คนนั้นถือ · ไม่ส่งทั้งคู่ = ทุกใบ (บัญชี/การเงิน)"""
    fund_ids = [f.id for f in repo.list_funds(db, holder=holder)] if holder else None
    rows, total = repo.list_claims(db, fund_ids=fund_ids, created_by=employee_id, status=status,
                                   month=month, limit=limit, offset=offset)
    names = repo.employee_names(db, [x for c in rows for x in (c.created_by, c.approved_by)])
    return {"total": total, "items": [_claim_dict(c, repo.claim_items(db, c.id), names) for c in rows]}


@router.get("/claims/duplicate-check")
def duplicate_check(expense_type: str, event_date: str, truck_code: Optional[str] = None,
                    truck_plate: Optional[str] = None, ref_no: Optional[str] = None,
                    exclude_claim_id: Optional[int] = None, db: Session = Depends(get_db)):
    """เตือนทันทีขณะกรอก — รับแถวเดียว คืน {hard, soft} ของแถวนั้น"""
    from datetime import date as _d
    try:
        parsed = _d.fromisoformat(event_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="วันที่ไม่ถูกต้อง")
    type_row = repo.get_expense_type(db, expense_type)
    spec = (type_row.spec if type_row else None) or {}
    row = {"event_date": parsed, "truck_code": truck_code, "truck_plate": truck_plate,
           "ref_no": ref_no}
    hard_days = int(((spec.get("duplicate_check") or {}).get("hard") or {}).get("days", 365))
    prior = repo.find_prior_items(db, expense_type=expense_type, keys=[row], days=hard_days,
                                  exclude_claim_id=exclude_claim_id)
    return validate.split_duplicates([row], prior, spec)


@router.get("/claims/{form_id}")
def get_claim(form_id: str, db: Session = Depends(get_db)):
    claim = repo.get_claim(db, form_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="ไม่พบใบเบิก")
    return _out(db, claim)


# ---------------------------- transitions ----------------------------

from datetime import datetime, timezone  # noqa: E402

from schemas.finance_petty_schema import ApproveIn, DdIn, ReturnIn, ReviewIn  # noqa: E402
from services.finance import petty_mail as mail  # noqa: E402
from services.finance import petty_validate as validate  # noqa: E402



def _gate(db: Session, claim, items, stage: str, attachments: dict) -> dict:
    """ด่านของใบนี้ตาม spec ของประเภท — BE เป็นผู้ตัดสิน ไม่เชื่อ FE"""
    type_row = repo.get_expense_type(db, claim.expense_type)
    spec = (type_row.spec if type_row else None) or {}
    item_dicts = [{"event_date": i.event_date, "truck_code": i.truck_code,
                   "truck_plate": i.truck_plate, "truck_type": i.truck_type,
                   "ref_no": i.ref_no, "rate": i.rate, "qty": i.qty,
                   "remark": i.remark, "driver_name": i.driver_name or claim.driver_name} for i in items]
    result = validate.validate_claim(
        {"amount": claim.amount, "dd_no": claim.dd_no, "receipt_no": claim.receipt_no,
         "expense_type": claim.expense_type, "detail": claim.detail or {},
         "payee_name": claim.payee_name, "driver_name": claim.driver_name},
        item_dicts, spec, stage, attachments or {})
    hard_days = int(((spec.get("duplicate_check") or {}).get("hard") or {}).get("days", 365))
    prior = repo.find_prior_items(db, expense_type=claim.expense_type, keys=item_dicts,
                                  days=hard_days, exclude_claim_id=claim.id)
    result["duplicates"] = validate.split_duplicates(item_dicts, prior, spec)
    return result


def _raise_if_blocked(gate: dict, *, check_duplicates: bool, override_reason,
                      notify=None) -> None:
    """notify = callable(kind, payload) สำหรับแจ้งเจ้าหน้าที่ก่อนจะโยน error ออกไป"""
    if gate["missing"]:
        if notify:
            notify("missing", gate["missing"])
        raise HTTPException(status_code=403, detail={"error": "เอกสารไม่ครบ อนุมัติไม่ได้",
                                                     "missing": gate["missing"]})
    if not check_duplicates:
        return
    dups = gate["duplicates"]
    if dups["hard"]:
        if notify:
            notify("duplicate", dups["hard"])
        raise HTTPException(status_code=409, detail={"error": "มีรายการที่เบิกไปแล้ว",
                                                    "duplicates": dups["hard"]})
    if dups["soft"] and not override_reason:
        if notify:
            notify("duplicate", dups["soft"])
        raise HTTPException(status_code=422,
                            detail={"error": "มีรายการที่อาจซ้ำ ยืนยันทับได้แต่ต้องระบุเหตุผล",
                                    "duplicates": dups["soft"]})


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
    _reject_incomplete_rows(db, claim.expense_type, items)
    try:
        out = repo.update_draft(db, claim, body, items, actor=payload.created_by)
    except L.PettyRuleError as exc:
        raise _rule_error(exc)
    return _out(db, out)


@router.post("/claims/{form_id}/approve")
def approve_claim(form_id: str, payload: ApproveIn, db: Session = Depends(get_db),
                  background: BackgroundTasks = None):
    claim = _claim_or_404(db, form_id)
    _require_holder(db, claim, payload.employee_id)
    items = repo.claim_items(db, claim.id)
    gate = _gate(db, claim, items, validate.STAGE_APPROVE, payload.attachments)

    clerk_email = repo.employee_email(db, claim.created_by)
    approver_email = repo.employee_email(db, payload.employee_id)

    def _notify(kind: str, rows):
        # แจ้งทั้งเจ้าหน้าที่ผู้เปิดใบและผู้อนุมัติ — เจ้าหน้าที่คือคนที่ต้องแก้
        send = (mail.notify_gate_blocked if kind == "missing" else mail.notify_duplicate)
        if background is not None:
            background.add_task(send, claim.form_id, rows, clerk_email, approver_email)
        else:
            send(claim.form_id, rows, clerk_email, approver_email)

    _raise_if_blocked(gate, check_duplicates=True, override_reason=payload.dup_override_reason,
                      notify=_notify)

    # flow v2: อนุมัติ = จ่ายเงินในจังหวะเดียวกัน (สลิปโอนอยู่ในด่าน APPROVE อยู่แล้ว)
    fields = {"approved_by": payload.employee_id, "transfer_date": payload.transfer_date or L.bkk_today()}
    if payload.dup_override_reason:
        fields["dup_override_reason"] = payload.dup_override_reason
    try:
        out = repo.set_status(db, claim, L.APPROVED, payload.employee_id, "APPROVE", fields=fields)
    except L.PettyRuleError as exc:
        raise _rule_error(exc)
    return _out(db, out)


@router.post("/claims/{form_id}/return")
def return_claim(form_id: str, payload: ReturnIn, db: Session = Depends(get_db),
                 background: BackgroundTasks = None):
    claim = _claim_or_404(db, form_id)
    _require_holder(db, claim, payload.employee_id)
    clerk_email = repo.employee_email(db, claim.created_by)
    try:
        out = repo.set_status(db, claim, L.RETURNED, payload.employee_id, "RETURN",
                              fields={"return_reason": payload.reason}, remark=payload.reason)
    except L.PettyRuleError as exc:
        raise _rule_error(exc)
    if background is not None:
        background.add_task(mail.notify_returned, out.form_id, payload.reason, clerk_email)
    else:
        mail.notify_returned(out.form_id, payload.reason, clerk_email)
    return _out(db, out)


@router.post("/claims/{form_id}/dd")
def set_claim_dd(form_id: str, payload: DdIn, db: Session = Depends(get_db)):
    """เจ้าของเงินสดใส่เลข DD ระหว่างรอส่งเคลียร์ — ด่านตอนรวมรอบบังคับ DD เมื่อยอดถึงเกณฑ์ของประเภท"""
    claim = _claim_or_404(db, form_id)
    _require_holder(db, claim, payload.employee_id)
    try:
        out = repo.set_dd(db, claim, payload.dd_no.strip(), payload.employee_id)
    except L.PettyRuleError as exc:
        raise _rule_error(exc)
    return _out(db, out)


def _snapshot_dd(db: Session, claim) -> None:
    """เก็บรายละเอียด DD ที่ดึงได้ตอนส่งบัญชี เพื่อให้ใบที่ส่งไปแล้วไม่เปลี่ยนตามที่ ATMS แก้ย้อนหลัง

    ล้มเหลวไม่ทำให้การส่งบัญชีล้ม — DD เป็นข้อมูลอ้างอิง ไม่ใช่เงื่อนไขของการส่ง
    """
    if not claim.dd_no:
        return
    try:
        from datetime import datetime as _dt

        from services.finance import petty_atms
        type_row = repo.get_expense_type(db, claim.expense_type)
        spec = ((type_row.spec if type_row else None) or {}).get("atms", {})
        dd = petty_atms.fetch_dd(claim.dd_no, atms_spec=spec)
        if not dd.get("found"):
            return
        claim.dd_snapshot = {
            "fetched_at": _dt.now(timezone.utc).isoformat(),
            "dd_no": dd["dd_no"], "received_date": dd["received_date"],
            "warehouse": dd["warehouse"], "supplier": dd["supplier"],
            "total": float(dd["total"]), "unit": dd["unit"], "vat": dd["vat"],
            "lines": [{k: (float(v) if isinstance(v, Decimal) else v) for k, v in line.items()}
                      for line in dd["lines"]],
        }
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()  # ไม่งั้น session ค้างสถานะ PendingRollback แล้วคำตอบที่ส่งกลับพังทั้งที่สถานะเปลี่ยนไปแล้ว
        logger.warning("DD snapshot failed for %s: %s", claim.form_id, exc)


@router.post("/claims/{form_id}/review")
def review_claim(form_id: str, payload: ReviewIn, db: Session = Depends(get_db)):
    """บัญชีตรวจทีละใบ — ผ่าน หรือตีกลับให้เจ้าหน้าที่ผู้เปิดใบแก้ (แก้แล้วส่งตรงกลับบัญชี)"""
    claim = _claim_or_404(db, form_id)
    if payload.action == "SEND_BACK":
        if not payload.remark:
            raise HTTPException(status_code=422, detail="ส่งกลับต้องระบุเหตุผล")
        try:
            out = repo.set_status(db, claim, L.SENT_BACK, payload.employee_id, "SEND_BACK",
                                  fields={"review_remark": payload.remark}, remark=payload.remark)
        except L.PettyRuleError as exc:
            raise _rule_error(exc)
        return _out(db, out)
    if claim.status != L.AWAITING_REVIEW:
        raise HTTPException(status_code=409,
                            detail=f"ตรวจได้เฉพาะใบที่รอบัญชีตรวจ ใบนี้สถานะ "
                                   f"{L.STATUS_LABELS.get(claim.status, claim.status)}")
    # PASS: ใบเป็น "ตรวจผ่าน รอตั้งเบิก" · ใบสุดท้ายของรอบผ่านเมื่อไร รอบพร้อมตั้งเบิก
    try:
        out = repo.review_pass(db, claim, payload.employee_id, payload.remark)
    except L.PettyRuleError as exc:
        raise _rule_error(exc)
    return _out(db, out)


# ---------------------------- ยอดคงเหลือ และรอบคืนยอด ----------------------------

from decimal import Decimal  # noqa: E402

from models.finance_petty_model import FinPettyBatch  # noqa: E402
from schemas.finance_petty_schema import PayIn, RoundActionIn, RoundIn, RoundRejectIn, VoucherIn  # noqa: E402
from services.finance import petty_clear as clear  # noqa: E402


@router.get("/funds/{fund_id}/balance")
def fund_balance(fund_id: int, db: Session = Depends(get_db)):
    try:
        bal = repo.fund_balance(db, fund_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {k: (float(v) if isinstance(v, Decimal) else v) for k, v in bal.items()}


def _iso(value):
    return value.isoformat() if value is not None else None


def _round_dict(db: Session, b, fund=None, claims=None, names: dict | None = None) -> dict:
    fund = fund or repo.get_fund(db, b.fund_id)
    holder = fund.holder_employee_id if fund else None
    names = names if names is not None else repo.employee_names(
        db, [holder, b.submitted_by, b.clear_approved_by, b.vouchered_by, b.paid_by])
    body = {
        "id": b.id, "batch_no": b.batch_no, "fund_id": b.fund_id, "status": b.status,
        "status_label": L.BATCH_STATUS_LABELS.get(b.status, b.status),
        "branch_code": fund.branch_code if fund else None, "division_code": fund.division_code if fund else None,
        "holder_employee_id": holder, "holder_name": names.get(holder),
        "total_amount": float(b.total_amount), "remark": b.remark,
        "submitted_by": b.submitted_by, "submitted_at": _iso(b.submitted_at),
        "clear_required_level": b.clear_required_level,
        "clear_approved_by": b.clear_approved_by, "clear_approved_by_name": names.get(b.clear_approved_by),
        "clear_approved_at": _iso(b.clear_approved_at), "clear_reject_reason": b.clear_reject_reason,
        "voucher_no": b.voucher_no, "vouchered_by": b.vouchered_by, "vouchered_at": _iso(b.vouchered_at),
        "pay_date": str(b.pay_date) if b.pay_date else None, "paid_by": b.paid_by, "paid_at": _iso(b.paid_at),
        "closed_by": b.closed_by, "closed_at": _iso(b.closed_at),
    }
    if claims is not None:
        body["claim_count"] = len(claims)
    return body


def _round_out(db: Session, b, employee_id: Optional[str] = None) -> dict:
    claims = repo.round_claims(db, b.id)
    body = _round_dict(db, b, claims=claims)
    claim_names = repo.employee_names(db, [x for c in claims for x in (c.created_by, c.approved_by)])
    body["claims"] = [_claim_dict(c, repo.claim_items(db, c.id), claim_names) for c in claims]
    if employee_id:
        body["can_clear"] = _may_clear(db, b, employee_id, fresh=False)
    return body


def _round_or_404(db: Session, batch_id: int):
    batch = repo.get_round(db, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="ไม่พบรอบส่งเคลียร์")
    return batch


def _required_level(people: dict, holder_id: Optional[str]) -> int:
    return L.clear_required_level((people.get(holder_id) or {}).get("level"))


def _may_clear(db: Session, batch, employee_id: str, fresh: bool = True) -> bool:
    """หัวหน้าตามสายงานของเจ้าของเงินสด (engine เดียวกับ Advance) · รอบต้องรออนุมัติเคลียร์อยู่"""
    if batch.status != L.BATCH_CLEAR_PENDING:
        return False
    fund = repo.get_fund(db, batch.fund_id)
    if fund is None:
        return False
    people, mappings = repo.line_context(db, fresh=fresh)
    required = batch.clear_required_level or _required_level(people, fund.holder_employee_id)
    return clear.can_clear(people, mappings, fund.holder_employee_id, required, employee_id)


@router.post("/batches", status_code=201)
def create_round(payload: RoundIn, db: Session = Depends(get_db), background: BackgroundTasks = None):
    """เจ้าของเงินสดรวมใบที่อนุมัติจ่ายแล้วเป็นรอบ (พร้อมใบปะหน้า) ส่งให้หัวหน้าตามสายงานอนุมัติเคลียร์"""
    fund = repo.get_fund(db, payload.fund_id)
    if fund is None:
        raise HTTPException(status_code=404, detail="ไม่พบกองทุน")
    if fund.holder_employee_id != payload.employee_id:
        raise HTTPException(status_code=403, detail="ส่งเคลียร์ได้เฉพาะเจ้าของเงินสดของกองทุนนี้")

    blocked = []
    for claim_id in dict.fromkeys(payload.claim_ids):
        claim = db.query(repo.FinPettyClaim).filter(repo.FinPettyClaim.id == claim_id).first()
        if claim is None or claim.status != L.APPROVED or claim.fund_id != fund.id:
            continue  # ให้ repo.create_round บอกเหตุผล (404/409) ด้วยข้อความเดียวกันทุกที่
        gate = _gate(db, claim, repo.claim_items(db, claim.id), validate.STAGE_SUBMIT_ACC,
                     payload.attachments.get(claim.form_id, {}))
        if gate["missing"]:
            blocked.append({"form_id": claim.form_id, "missing": gate["missing"]})
    if blocked:
        raise HTTPException(status_code=403, detail={"error": "ส่งเคลียร์ไม่ได้ เอกสารบางใบยังไม่ครบ",
                                                     "claims": blocked})

    people, mappings = repo.line_context(db, fresh=True)
    required = _required_level(people, fund.holder_employee_id)
    try:
        batch = repo.create_round(db, fund.id, payload.claim_ids, payload.employee_id, payload.remark,
                                  required_level=required)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except L.PettyRuleError as exc:
        raise _rule_error(exc)

    heads = clear.clear_notify(people, mappings, fund.holder_employee_id, required)
    emails = [repo.employee_email(db, p["employee_id"]) for p in heads]
    holder_name = repo.employee_names(db, [fund.holder_employee_id]).get(fund.holder_employee_id)
    args = (batch.id, batch.batch_no, holder_name, len(payload.claim_ids), batch.total_amount, emails)
    if background is not None:
        background.add_task(mail.notify_round_submitted, *args)
    else:
        mail.notify_round_submitted(*args)
    return _round_out(db, batch)


@router.get("/batches")
def list_rounds(fund_id: Optional[int] = None, status: Optional[str] = None, holder: Optional[str] = None,
                clear_approver: Optional[str] = None, limit: int = Query(default=50, le=200), offset: int = 0,
                db: Session = Depends(get_db)):
    """holder = รอบของกองทุนที่คนนั้นถือ · clear_approver = รอบที่คนนั้นอนุมัติเคลียร์ได้ · status คั่นด้วย ,"""
    fund_ids = None
    if holder:
        fund_ids = [f.id for f in repo.list_funds(db, holder=holder, active_only=False)]
    if fund_id:
        fund_ids = [fund_id] if fund_ids is None else [x for x in fund_ids if x == fund_id]
    if clear_approver:
        status = L.BATCH_CLEAR_PENDING
    rows = repo.list_rounds(db, fund_ids=fund_ids, status=status, limit=limit, offset=offset)
    if clear_approver:
        rows = [b for b in rows if _may_clear(db, b, clear_approver, fresh=False)]
    out = []
    for b in rows:
        out.append(_round_dict(db, b, claims=repo.round_claims(db, b.id)))
    return out


@router.get("/batches/{batch_id}")
def get_round(batch_id: int, employee_id: Optional[str] = None, db: Session = Depends(get_db)):
    """รอบ + ใบทั้งหมดในรอบ · ส่ง employee_id มาเพื่อรู้ว่าคนนั้นกดอนุมัติเคลียร์ได้ไหม (can_clear)"""
    return _round_out(db, _round_or_404(db, batch_id), employee_id)


@router.post("/batches/{batch_id}/clear-approve")
def clear_approve_round(batch_id: int, payload: RoundActionIn, db: Session = Depends(get_db)):
    batch = _round_or_404(db, batch_id)
    if batch.status == L.BATCH_CLEAR_PENDING and not _may_clear(db, batch, payload.employee_id):
        raise HTTPException(status_code=403, detail="อนุมัติเคลียร์ได้เฉพาะผู้จัดการตามสายงานของเจ้าของเงินสด")
    try:
        out = repo.clear_approve_round(db, batch, payload.employee_id)
    except L.PettyRuleError as exc:
        raise _rule_error(exc)
    for claim in repo.round_claims(db, out.id):
        _snapshot_dd(db, claim)  # ส่งถึงบัญชีแล้ว ล็อกรายละเอียด DD ไว้ ไม่ให้เปลี่ยนตามที่ ATMS แก้ย้อนหลัง
    return _round_out(db, out)


@router.post("/batches/{batch_id}/clear-reject")
def clear_reject_round(batch_id: int, payload: RoundRejectIn, db: Session = Depends(get_db),
                       background: BackgroundTasks = None):
    batch = _round_or_404(db, batch_id)
    if batch.status == L.BATCH_CLEAR_PENDING and not _may_clear(db, batch, payload.employee_id):
        raise HTTPException(status_code=403, detail="ไม่อนุมัติได้เฉพาะผู้จัดการตามสายงานของเจ้าของเงินสด")
    try:
        out = repo.clear_reject_round(db, batch, payload.employee_id, payload.reason.strip())
    except L.PettyRuleError as exc:
        raise _rule_error(exc)
    fund = repo.get_fund(db, out.fund_id)
    holder_email = repo.employee_email(db, fund.holder_employee_id) if fund else None
    args = (out.id, out.batch_no, out.clear_reject_reason, holder_email)
    if background is not None:
        background.add_task(mail.notify_round_rejected, *args)
    else:
        mail.notify_round_rejected(*args)
    return _round_out(db, out)


@router.post("/batches/{batch_id}/voucher")
def voucher_round(batch_id: int, payload: VoucherIn, db: Session = Depends(get_db)):
    """บัญชีตั้งเบิกทั้งรอบ (ทุกใบต้องตรวจผ่านแล้ว)"""
    batch = _round_or_404(db, batch_id)
    try:
        out = repo.voucher_round(db, batch, payload.employee_id, payload.voucher_no.strip())
    except L.PettyRuleError as exc:
        raise _rule_error(exc)
    return _round_out(db, out)


@router.post("/batches/{batch_id}/pay")
def pay_round(batch_id: int, payload: PayIn, db: Session = Depends(get_db)):
    """การเงินทำจ่ายคืนเจ้าของเงินสด → ปิดรอบและทุกใบในรอบ"""
    batch = _round_or_404(db, batch_id)
    try:
        out = repo.pay_round(db, batch, payload.employee_id, payload.pay_date)
    except L.PettyRuleError as exc:
        raise _rule_error(exc)
    return _round_out(db, out)


@router.get("/trucks")
def search_trucks(q: str = Query(default=""), limit: int = Query(default=20, le=50)):
    """ค้นรถจาก master ATMS — ค้นได้ทั้งเบอร์รถและทะเบียน · Mongo ล่มคืนลิสต์ว่าง ไม่ทำให้ฟอร์มพัง"""
    from services.finance import petty_master
    return petty_master.search_trucks(q, limit=limit)


@router.get("/dd/{dd_no}")
def get_dd(dd_no: str, expense_type: Optional[str] = None, claim_amount: Optional[float] = None,
           db: Session = Depends(get_db)):
    """รายละเอียด DD จาก ATMS — อ่านอย่างเดียว · ไม่พบ/Mongo ล่ม ก็คืนรูปทรงเดิมพร้อมเหตุผล"""
    from services.finance import petty_atms
    spec = {}
    if expense_type:
        row = repo.get_expense_type(db, expense_type)
        spec = ((row.spec if row else None) or {}).get("atms", {})
    dd = petty_atms.fetch_dd(dd_no, atms_spec=spec)
    body = {k: (float(v) if isinstance(v, Decimal) else v) for k, v in dd.items() if k != "lines"}
    body["lines"] = [{k: (float(v) if isinstance(v, Decimal) else v) for k, v in line.items()}
                     for line in dd["lines"]]
    if claim_amount is not None:
        cmp_result = petty_atms.compare_total(dd, Decimal(str(claim_amount)))
        body["total_match"] = cmp_result["match"]
        body["total_difference"] = float(cmp_result["difference"])
    return body
