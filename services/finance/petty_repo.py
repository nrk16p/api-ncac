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


# ---------------------------- claims ----------------------------

from datetime import date, date as _date  # noqa: E402
from decimal import Decimal  # noqa: E402

from sqlalchemy import func  # noqa: E402

from models.finance_petty_model import FinPettyClaim, FinPettyClaimItem, FinPettyLog  # noqa: E402
from services.finance import petty_logic as L  # noqa: E402


def log(db: Session, claim_id: int, action: str, actor: str,
        changes: Optional[dict] = None, remark: Optional[str] = None) -> None:
    db.add(FinPettyLog(claim_id=claim_id, action=action, action_by=actor,
                       changes=changes, remark=remark))


def _item_rows(claim_id: int, items: list[dict]) -> list[FinPettyClaimItem]:
    rows = []
    for index, raw in enumerate(items, start=1):
        qty = Decimal(str(raw.get("qty", 1)))
        rate = Decimal(str(raw["rate"]))
        rows.append(FinPettyClaimItem(
            claim_id=claim_id, line_no=index, event_date=raw["event_date"],
            truck_code=raw.get("truck_code"), truck_plate=raw.get("truck_plate"),
            truck_type=raw.get("truck_type"), ref_no=raw.get("ref_no"),
            qty=qty, rate=rate, amount=L.line_amount(qty, rate), remark=raw.get("remark"),
            driver_name=raw.get("driver_name"), driver_role=raw.get("driver_role"),
            tire_position=raw.get("tire_position"),
        ))
    return rows


def create_claim(db: Session, payload: dict, items: list[dict], actor: str) -> FinPettyClaim:
    """เปิดใบ: ออกเลขจาก form_sequences → เขียนหัวใบ → เขียนรายการ → สรุปยอดและช่วงวัน"""
    from routes.forms.form_submission_routes import generate_form_id  # นำเข้าในฟังก์ชัน กัน import วน

    fund = get_fund(db, payload["fund_id"])
    if fund is None or not fund.is_active:
        raise LookupError("ไม่พบกองทุน หรือกองทุนถูกปิดอยู่")

    form_id = generate_form_id(db, L.petty_form_code(fund.branch_code, fund.division_code))
    period_from, period_to = L.period_of(items)
    claim = FinPettyClaim(
        form_id=form_id, status=L.PENDING_APPROVAL,
        amount=L.claim_total(items), period_from=period_from, period_to=period_to,
        **payload,
    )
    db.add(claim)
    db.flush()
    for row in _item_rows(claim.id, items):
        db.add(row)
    log(db, claim.id, "CREATE", actor, changes={"amount": str(claim.amount), "items": len(items)})
    db.commit()
    db.refresh(claim)
    return claim


def get_claim(db: Session, form_id: str) -> Optional[FinPettyClaim]:
    return db.query(FinPettyClaim).filter(FinPettyClaim.form_id == form_id).first()


def claim_items(db: Session, claim_id: int) -> list[FinPettyClaimItem]:
    return (db.query(FinPettyClaimItem)
            .filter(FinPettyClaimItem.claim_id == claim_id)
            .order_by(FinPettyClaimItem.line_no).all())


def replace_items(db: Session, claim_id: int, items: list[dict]) -> Decimal:
    db.query(FinPettyClaimItem).filter(FinPettyClaimItem.claim_id == claim_id).delete()
    for row in _item_rows(claim_id, items):
        db.add(row)
    total = L.claim_total(items)
    period_from, period_to = L.period_of(items)
    claim = db.query(FinPettyClaim).filter(FinPettyClaim.id == claim_id).first()
    claim.amount, claim.period_from, claim.period_to = total, period_from, period_to
    db.commit()
    return total


def list_claims(db: Session, *, fund_ids: Optional[list[int]], created_by: Optional[str],
                status: Optional[str], month: Optional[str], limit: int, offset: int):
    q = db.query(FinPettyClaim)
    if fund_ids is not None:
        q = q.filter(FinPettyClaim.fund_id.in_(fund_ids or [-1]))
    if created_by:
        q = q.filter(FinPettyClaim.created_by == created_by)
    if status:
        q = q.filter(FinPettyClaim.status == status)
    if month:
        year, mon = (int(x) for x in month.split("-"))
        start = _date(year, mon, 1)
        end = _date(year + (mon == 12), 1 if mon == 12 else mon + 1, 1)
        q = q.filter(FinPettyClaim.expense_date >= start, FinPettyClaim.expense_date < end)
    total = q.with_entities(func.count(FinPettyClaim.id)).scalar() or 0
    rows = q.order_by(FinPettyClaim.id.desc()).limit(limit).offset(offset).all()
    return rows, total


# ---------------------------- transitions ----------------------------

from contextlib import contextmanager  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

# flow v2: อนุมัติ = จ่ายพร้อมกัน จึงประทับทั้งเวลาอนุมัติและเวลาแนบสลิปในจังหวะเดียว
_NOW_FIELDS = {
    "APPROVED": ("approved_at", "transfer_slip_at"),
    "AWAITING_REVIEW": ("submitted_to_acc_at",),
}


def set_status(db: Session, claim: FinPettyClaim, target: str, actor: str, action: str,
               fields: Optional[dict] = None, remark: Optional[str] = None,
               log_extra: Optional[dict] = None, commit: bool = True) -> FinPettyClaim:
    """จังหวะเดียวที่สถานะเปลี่ยนได้ — ทุก endpoint ต้องผ่านทางนี้ จึงมี log และกฎครบเสมอ

    log_extra = ค่าที่ไม่ใช่ช่องของใบแต่ควรอยู่ใน log ด้วย เช่น เลขที่ทำจ่ายของรอบ
    commit=False = งานทั้งรอบที่วนหลายใบ ผู้เรียก commit ครั้งเดียวตอนจบ (ดู _all_or_nothing)"""
    L.assert_transition(claim.status, target)
    before = claim.status
    for key, value in (fields or {}).items():
        setattr(claim, key, value)
    for stamp in _NOW_FIELDS.get(target, ()):
        if getattr(claim, stamp) is None:
            setattr(claim, stamp, datetime.now(timezone.utc))
    if target in ("REVIEWED", "SENT_BACK"):
        claim.reviewed_at, claim.reviewed_by = datetime.now(timezone.utc), actor
    claim.status = target
    changes = {"from": before, "to": target}
    changes.update({k: str(v) for k, v in (fields or {}).items()})
    changes.update(log_extra or {})
    log(db, claim.id, action, actor, changes=changes, remark=remark)
    if commit:
        db.commit()
        db.refresh(claim)
    return claim


@contextmanager
def _all_or_nothing(db: Session):
    """งานทั้งรอบสำเร็จพร้อมกันใน commit เดียว หรือไม่เปลี่ยนอะไรเลย
    ใบที่ 2 ล้มต้องไม่เหลือรอบที่เปลี่ยนสถานะไปแล้วกับใบแรกที่เปลี่ยนไปครึ่งทาง"""
    try:
        yield
        db.commit()
    except Exception:
        db.rollback()
        raise


_EDITABLE = ("PENDING_APPROVAL", "RETURNED", "SENT_BACK")


def update_draft(db: Session, claim: FinPettyClaim, payload: dict, items: list[dict],
                 actor: str) -> FinPettyClaim:
    """แก้ได้ตอนรออนุมัติ ถูกเจ้าของเงินสดตีกลับ หรือถูกบัญชีตีกลับ

    ใบที่บัญชีตีกลับ แก้แล้วส่งตรงกลับบัญชี ไม่ต้องอนุมัติใหม่ (ผู้ใช้สั่ง 29/10/2026) · ที่เหลือกลับเข้าคิวอนุมัติ
    """
    if claim.status not in _EDITABLE:
        raise L.PettyRuleError(
            f"แก้ไขไม่ได้ ใบนี้สถานะ {L.STATUS_LABELS.get(claim.status, claim.status)} แล้ว", 409)
    if claim.status == L.SENT_BACK:
        for key, value in payload.items():
            setattr(claim, key, value)
        if items:
            replace_items(db, claim.id, items)
            db.refresh(claim)
        claim.status = L.AWAITING_REVIEW
        log(db, claim.id, "RESUBMIT_ACC", actor, changes={"amount": str(claim.amount)})
        db.commit()
        db.refresh(claim)
        return claim
    was_returned = claim.status == "RETURNED"
    for key, value in payload.items():
        setattr(claim, key, value)
    if items:
        replace_items(db, claim.id, items)
        db.refresh(claim)
    claim.status = L.PENDING_APPROVAL
    log(db, claim.id, "RESUBMIT" if was_returned else "UPDATE", actor,
        changes={"amount": str(claim.amount)})
    db.commit()
    db.refresh(claim)
    return claim


# ---------------------------- ยอด Advance และรอบคืนยอด ----------------------------

from models.finance_model import FinAdvance  # noqa: E402
from models.finance_petty_model import FinPettyBatch  # noqa: E402

# ยอดที่ Advance จ่ายออกไปแล้วและยังไม่ปิด — petty หักจากยอดนี้ (spec §6)
OUTSTANDING_FIN_STATUSES = ("PAID", "CLEARING_SUBMITTED", "SENT_BACK")
# สถานะที่ถือเงินไว้ของ petty: ตั้งแต่เจ้าของเงินสดจ่ายออกไป จนการเงินทำจ่ายคืนทั้งรอบ
COMMITTED_STATUSES = ("APPROVED", "CLEAR_PENDING", "AWAITING_REVIEW", "SENT_BACK", "REVIEWED")

_ZERO = Decimal("0.00")


def advance_outstanding(db: Session, employee_id: str) -> Decimal:
    """อ่าน fin_advances เท่านั้น ไม่เขียน — ค่าที่บัญชี/การเงินกรอกคือค่าจริง

    ผูกคนด้วย form_submissions.created_by ของใบ ADV นั้น (fin_advances ไม่มีคอลัมน์ employee_id)
    """
    from models.master_model import FormSubmission
    total = (db.query(func.coalesce(func.sum(FinAdvance.amount_paid), 0))
             .join(FormSubmission, FormSubmission.form_id == FinAdvance.form_id)
             .filter(FinAdvance.fin_status.in_(OUTSTANDING_FIN_STATUSES),
                     FormSubmission.created_by == employee_id)
             .scalar())
    return Decimal(str(total or 0)).quantize(Decimal("0.01"))


def petty_committed(db: Session, employee_id: str) -> Decimal:
    """ยอดที่ใบ petty ของกองทุนที่คนนี้ถือกินไว้อยู่"""
    total = (db.query(func.coalesce(func.sum(FinPettyClaim.amount), 0))
             .join(FinPettyFund, FinPettyFund.id == FinPettyClaim.fund_id)
             .filter(FinPettyFund.holder_employee_id == employee_id,
                     FinPettyClaim.status.in_(COMMITTED_STATUSES))
             .scalar())
    return Decimal(str(total or 0)).quantize(Decimal("0.01"))


def fund_balance(db: Session, fund_id: int) -> dict:
    fund = get_fund(db, fund_id)
    if fund is None:
        raise LookupError("ไม่พบกองทุน")
    outstanding = advance_outstanding(db, fund.holder_employee_id)
    committed = petty_committed(db, fund.holder_employee_id)
    return {
        "fund_id": fund.id,
        "holder_employee_id": fund.holder_employee_id,
        "advance_outstanding": outstanding,
        "petty_committed": committed,
        "available": (outstanding - committed).quantize(Decimal("0.01")),
        "fund_limit": fund.fund_limit,
    }


def line_context(db: Session, fresh: bool = True):
    """(people, mappings) ของสายงาน — ชุดเดียวกับที่ Advance ใช้ตัดสินผู้อนุมัติ

    ตัดสินสิทธิ์กดต้อง fresh=True (ถอดสิทธิ์/ย้ายแผนกมีผลทันที) · หน้ารายการใช้แคช 60 วิได้
    """
    from services.finance import approval_repo
    ctx = approval_repo.load_context(db, fresh=fresh)
    return ctx.people, ctx.mappings


def _yymm_bkk() -> int:
    """ปีเดือนตามเวลาไทย — นาฬิกาเดียวกับเลขที่ใบ (เซิร์ฟเวอร์เป็น UTC ข้ามเดือนตอนตี 0–7 จะผิด)"""
    from routes.forms.form_submission_routes import _now_bkk
    return int(_now_bkk().strftime("%y%m"))


def get_round(db: Session, batch_id: int) -> Optional[FinPettyBatch]:
    return db.query(FinPettyBatch).filter(FinPettyBatch.id == batch_id).first()


def round_claims(db: Session, batch_id: int) -> list[FinPettyClaim]:
    return (db.query(FinPettyClaim).filter(FinPettyClaim.batch_id == batch_id)
            .order_by(FinPettyClaim.id).all())


def list_rounds(db: Session, *, fund_ids: Optional[list[int]] = None, status: Optional[str] = None,
                limit: int = 50, offset: int = 0) -> list[FinPettyBatch]:
    q = db.query(FinPettyBatch)
    if fund_ids is not None:
        q = q.filter(FinPettyBatch.fund_id.in_(fund_ids or [-1]))
    if status:
        q = q.filter(FinPettyBatch.status.in_([x for x in status.split(",") if x]))
    return q.order_by(FinPettyBatch.id.desc()).offset(offset).limit(limit).all()


def rounds_with_doc_no(db: Session, field: str, value: str, exclude_id: Optional[int] = None) -> list[str]:
    """เลขรอบอื่นที่ใช้เลขเอกสารนี้ (ไม่สนตัวพิมพ์ใหญ่เล็ก ตัดช่องว่างหัวท้าย) · field ต้องผ่านการกรองมาแล้ว"""
    column = getattr(FinPettyBatch, field)
    q = db.query(FinPettyBatch.batch_no).filter(func.upper(func.trim(column)) == value.strip().upper())
    if exclude_id:
        q = q.filter(FinPettyBatch.id != exclude_id)
    return [no for (no,) in q.order_by(FinPettyBatch.batch_no).limit(10).all()]


def _set_round_status(db: Session, batch: FinPettyBatch, target: str) -> None:
    L.assert_batch_transition(batch.status, target)
    batch.status = target


def _last_round_number(db: Session, branch: str, division: str, yymm: int) -> int:
    """เลขรอบสูงสุดของ สาขา-แผนก-เดือน นี้ ทุกกองทุนรวมกัน
    เลขรอบ unique ทั้งตาราง แต่กองทุนซ้ำสาขา+แผนกได้เมื่อกองเก่าปิดแล้ว จึงนับต่อกองทุนไม่ได้"""
    prefix = L.batch_no(branch, division, yymm, 0)[:-1]  # "LB-B02-2610-B"
    rows = db.query(FinPettyBatch.batch_no).filter(FinPettyBatch.batch_no.like(f"{prefix}%")).all()
    numbers = [int(no[len(prefix):]) for (no,) in rows
               if no.startswith(prefix) and no[len(prefix):].isdigit()]
    return max(numbers, default=0)


def create_round(db: Session, fund_id: int, claim_ids: list[int], actor: str, remark: Optional[str],
                 required_level: int) -> FinPettyBatch:
    """เจ้าของเงินสดรวมใบที่อนุมัติจ่ายแล้วของกองทุนตัวเองเป็นรอบ ส่งให้หัวหน้าอนุมัติเคลียร์"""
    with _all_or_nothing(db):
        fund = get_fund(db, fund_id)
        if fund is None:
            raise LookupError("ไม่พบกองทุน")
        claims = db.query(FinPettyClaim).filter(FinPettyClaim.id.in_(claim_ids)).all()
        if len(claims) != len(set(claim_ids)):
            raise L.PettyRuleError("มีใบที่หาไม่พบในรายการที่เลือก", 404)
        for claim in claims:
            if claim.fund_id != fund_id:
                raise L.PettyRuleError(f"ใบ {claim.form_id} ไม่ได้อยู่ในกองทุนนี้", 409)
            if claim.status != L.APPROVED:
                raise L.PettyRuleError(
                    f"ใบ {claim.form_id} สถานะ {L.STATUS_LABELS.get(claim.status, claim.status)} "
                    f"ยังส่งเคลียร์ไม่ได้", 409)

        yymm = _yymm_bkk()
        batch = FinPettyBatch(
            fund_id=fund_id, remark=remark, status=L.BATCH_CLEAR_PENDING,
            batch_no=L.batch_no(fund.branch_code, fund.division_code, yymm,
                                _last_round_number(db, fund.branch_code, fund.division_code, yymm) + 1),
            total_amount=sum((c.amount for c in claims), _ZERO),
            submitted_by=actor, submitted_at=datetime.now(timezone.utc), clear_required_level=required_level,
        )
        db.add(batch)
        db.flush()
        for claim in claims:
            set_status(db, claim, L.CLEAR_PENDING, actor, "SUBMIT_ROUND",
                       fields={"batch_id": batch.id}, remark=batch.batch_no, commit=False)
    db.refresh(batch)
    return batch


def clear_approve_round(db: Session, batch: FinPettyBatch, actor: str) -> FinPettyBatch:
    """หัวหน้าอนุมัติเคลียร์ทั้งรอบ → ทุกใบเข้าคิวบัญชีตรวจ (สิทธิ์ตรวจที่ route ก่อนเรียก)"""
    with _all_or_nothing(db):
        _set_round_status(db, batch, L.BATCH_AWAITING_REVIEW)
        batch.clear_approved_by, batch.clear_approved_at = actor, datetime.now(timezone.utc)
        for claim in round_claims(db, batch.id):
            set_status(db, claim, L.AWAITING_REVIEW, actor, "CLEAR_APPROVE", remark=batch.batch_no, commit=False)
    db.refresh(batch)
    return batch


def clear_reject_round(db: Session, batch: FinPettyBatch, actor: str, reason: str) -> FinPettyBatch:
    """หัวหน้าไม่อนุมัติ → รอบจบ ใบกลับไปให้เจ้าของเงินสดแก้แล้วจัดรอบใหม่ (ยอดยังถูกกันไว้ เพราะจ่ายไปแล้ว)"""
    with _all_or_nothing(db):
        _set_round_status(db, batch, L.BATCH_CLEAR_REJECTED)
        batch.clear_approved_by, batch.clear_approved_at = actor, datetime.now(timezone.utc)
        batch.clear_reject_reason = reason
        for claim in round_claims(db, batch.id):
            set_status(db, claim, L.APPROVED, actor, "CLEAR_REJECT", fields={"batch_id": None}, remark=reason,
                       commit=False)
    db.refresh(batch)
    return batch


def review_pass(db: Session, claim: FinPettyClaim, actor: str, remark: Optional[str]) -> FinPettyClaim:
    """บัญชีตรวจผ่านทีละใบ · ใบสุดท้ายของรอบผ่านเมื่อไร รอบพร้อมตั้งเบิกทันที (ใบกับรอบลงพร้อมกัน)"""
    with _all_or_nothing(db):
        out = set_status(db, claim, L.REVIEWED, actor, "ACC_PASS", fields={"review_remark": remark},
                         remark=remark, commit=False)
        if out.batch_id is not None:
            batch = get_round(db, out.batch_id)
            siblings = round_claims(db, out.batch_id)
            if batch is not None and batch.status == L.BATCH_AWAITING_REVIEW \
                    and all(c.status == L.REVIEWED for c in siblings):
                _set_round_status(db, batch, L.BATCH_REVIEWED)
    db.refresh(out)
    return out


def voucher_round(db: Session, batch: FinPettyBatch, actor: str, voucher_no: Optional[str],
                  voucher_date) -> FinPettyBatch:
    """บัญชีตั้งเบิกทั้งรอบ · นับยอดใหม่ เพราะเจ้าหน้าที่อาจแก้ยอดตอนบัญชีตีกลับ
    สถานะใบไม่เปลี่ยน แต่ลง log ทุกใบ ให้ประวัติของแต่ละใบเห็นว่าตั้งเบิกแล้ว"""
    with _all_or_nothing(db):
        _set_round_status(db, batch, L.BATCH_VOUCHERED)
        batch.voucher_no, batch.voucher_date = voucher_no, voucher_date
        batch.vouchered_by, batch.vouchered_at = actor, datetime.now(timezone.utc)
        claims = round_claims(db, batch.id)
        batch.total_amount = sum((c.amount for c in claims), _ZERO)
        for claim in claims:
            log(db, claim.id, "VOUCHER", actor, remark=batch.batch_no,
                changes={"voucher_no": voucher_no, "voucher_date": str(voucher_date)})
    db.refresh(batch)
    return batch


def pay_round(db: Session, batch: FinPettyBatch, actor: str, pay_date,
              pay_doc_no: Optional[str] = None) -> FinPettyBatch:
    """การเงินทำจ่ายคืนเจ้าของเงินสดทั้งรอบ → ปิดรอบและทุกใบ ยอดคืนเข้า available เอง"""
    with _all_or_nothing(db):
        _set_round_status(db, batch, L.BATCH_CLOSED)
        now = datetime.now(timezone.utc)
        batch.pay_date, batch.pay_doc_no, batch.paid_by, batch.paid_at = pay_date, pay_doc_no, actor, now
        batch.closed_by, batch.closed_at = actor, now
        for claim in round_claims(db, batch.id):
            set_status(db, claim, L.CLOSED, actor, "PAY_CLOSE", remark=batch.batch_no,
                       log_extra={"pay_date": str(pay_date), "pay_doc_no": pay_doc_no}, commit=False)
    db.refresh(batch)
    return batch


def edit_doc_numbers(db: Session, batch: FinPettyBatch, actor: str, wanted: dict) -> FinPettyBatch:
    """แก้เลขเอกสารที่พิมพ์ผิดหลังตั้งเบิก/ทำจ่าย (สถานะไม่เปลี่ยน) · wanted = เฉพาะช่องที่หน้าเว็บส่งมา

    ลง log EDIT_DOC_NUMBERS ทุกใบในรอบด้วยเวลาเดียวกัน ประวัติของใบเห็นการแก้ และประวัติรอบอ่านจากใบแรก
    (ใบในรอบที่ตั้งเบิกแล้วไม่หลุดออกจากรอบอีก)"""
    current = {k: getattr(batch, k) for k in L.ROUND_DOC_LABELS}
    diffs = L.doc_number_changes(batch.status, current, wanted)
    if not diffs:
        return batch
    remark = L.doc_changes_text(diffs)
    changes = {k: [None if v is None else str(v) for v in pair] for k, pair in diffs.items()}
    now = datetime.now(timezone.utc)
    with _all_or_nothing(db):
        for key, (_, new) in diffs.items():
            setattr(batch, key, new)
        for claim in round_claims(db, batch.id):
            db.add(FinPettyLog(claim_id=claim.id, action="EDIT_DOC_NUMBERS", action_by=actor,
                               changes=changes, remark=remark, created_at=now))
    db.refresh(batch)
    return batch


ROUND_LOG_ACTIONS = ("VOUCHER", "PAY_CLOSE", "EDIT_DOC_NUMBERS")


def round_claim_logs(db: Session, batch_id: int, actions=ROUND_LOG_ACTIONS) -> list[FinPettyLog]:
    """log ระดับรอบที่ลงซ้ำทุกใบ (ตั้งเบิก จ่าย แก้เลขเอกสาร) — อ่านจากใบแรกพอ ไม่ต้องตัดตัวซ้ำ
    (ใบในรอบที่ตั้งเบิกแล้วไม่หลุดออกจากรอบอีก ใบแรกจึงมีครบทุกครั้ง)"""
    first = (db.query(func.min(FinPettyClaim.id)).filter(FinPettyClaim.batch_id == batch_id).scalar())
    if first is None:
        return []
    return (db.query(FinPettyLog)
            .filter(FinPettyLog.claim_id == first, FinPettyLog.action.in_(actions))
            .order_by(FinPettyLog.id).all())


def round_doc_edits(db: Session, batch_id: int) -> list[FinPettyLog]:
    """การแก้เลขเอกสารของรอบ เก่าสุดก่อน"""
    return round_claim_logs(db, batch_id, ("EDIT_DOC_NUMBERS",))


def set_dd(db: Session, claim: FinPettyClaim, dd_no: str, actor: str) -> FinPettyClaim:
    """เจ้าของเงินสดใส่เลข DD ก่อนรวมเป็นรอบ (ด่านส่งบัญชีบังคับ DD เมื่อยอดถึงเกณฑ์)"""
    if claim.status != L.APPROVED:
        raise L.PettyRuleError(
            f"ใส่เลข DD ได้เฉพาะใบที่อนุมัติจ่ายแล้วและยังไม่ส่งเคลียร์ ใบนี้สถานะ "
            f"{L.STATUS_LABELS.get(claim.status, claim.status)}", 409)
    before = claim.dd_no
    claim.dd_no = dd_no
    log(db, claim.id, "SET_DD", actor, changes={"dd_no": [before, dd_no]})
    db.commit()
    db.refresh(claim)
    return claim


# ---------------------------- ด่านซ้ำ: ดึงรายการที่อาจซ้ำ ----------------------------

from datetime import timedelta  # noqa: E402


def find_prior_items(db: Session, *, expense_type: str, keys: list[dict], days: int,
                     exclude_claim_id: Optional[int]) -> list[dict]:
    """ดึงรายการของประเภทเดียวกันที่อาจซ้ำ — จำกัดด้วยช่วงวันและรถที่อยู่ในใบนี้เท่านั้น

    ไม่ดึงทั้งตาราง: ช่วงวันมาจากรายการในใบ (ปกติไม่กี่วัน) แล้วปล่อยให้ index
    (event_date, truck_code) กับ (ref_no, truck_code, truck_plate) ทำงาน
    ด่านเข้มมองย้อนได้ไกลกว่าด่านอ่อน จึงขยายช่วงด้วย `days` ที่ผู้เรียกส่งมา
    """
    if not keys:
        return []
    dates = [k["event_date"] for k in keys if k.get("event_date")]
    if not dates:
        return []
    low, high = min(dates) - timedelta(days=days), max(dates) + timedelta(days=days)
    codes = {L.normalize_code(k.get("truck_code")) for k in keys}
    plates = {L.normalize_plate(k.get("truck_plate")) for k in keys}
    refs = {str(k.get("ref_no") or "").strip() for k in keys if k.get("ref_no")}

    # ค่าว่างไม่ใช่คีย์: รายการไม่มีรถ (แท็กซี่/ทางด่วน) ต้องไม่ดึงรายการไม่มีรถของคนอื่นมาทั้งคลัง
    codes.discard(""); plates.discard("")
    drivers = {" ".join(str(k.get("driver_name") or "").split()).lower() for k in keys} - {""}

    rows = (db.query(FinPettyClaimItem, FinPettyClaim.form_id, FinPettyClaim.payee_name, FinPettyClaim.amount,
                     FinPettyClaim.driver_name)
            .join(FinPettyClaim, FinPettyClaim.id == FinPettyClaimItem.claim_id)
            .filter(FinPettyClaim.expense_type == expense_type,
                    FinPettyClaim.status != L.RETURNED,
                    FinPettyClaimItem.event_date >= low,
                    FinPettyClaimItem.event_date <= high)
            .filter(FinPettyClaim.id != exclude_claim_id if exclude_claim_id else True)
            .limit(2000)  # ขอบเขตกันหลุด — ปกติได้ไม่กี่สิบแถว
            .all())

    out = []
    for item, form_id, payee_name, amount, claim_driver in rows:
        driver = item.driver_name or claim_driver  # แถวไม่ระบุ พจส. = คนขับของใบ (ค่าเดินทาง)
        same_truck = (L.normalize_code(item.truck_code) in codes
                      or L.normalize_plate(item.truck_plate) in plates)
        same_ref = (item.ref_no or "").strip() in refs
        same_driver = " ".join(str(driver or "").split()).lower() in drivers
        if not same_truck and not same_ref and not same_driver:
            continue
        out.append({"event_date": item.event_date, "truck_code": item.truck_code,
                    "truck_plate": item.truck_plate, "ref_no": item.ref_no, "form_id": form_id,
                    "driver_name": driver,
                    "payee_name": payee_name, "amount": float(amount) if amount is not None else None})
    return out


def month_sequence(db: Session, claim, items) -> dict[int, int]:
    """ใบชั่งเป็นใบที่เท่าไรของรถคันนั้นในเดือน (REQUIREMENT §3.1) — นับทุกใบของประเภทเดียวกันที่ไม่ถูกตีกลับ
    เรียงตามวันที่ชั่งแล้วลำดับแถว · คืน {item.id: ลำดับ}"""
    if not items:
        return {}
    months = {(i.event_date.year, i.event_date.month) for i in items if i.event_date}
    codes = {L.normalize_code(i.truck_code) for i in items if i.truck_code}
    if not months or not codes:
        return {}
    lows = [date(y, m, 1) for y, m in months]
    highs = [date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1) for y, m in months]
    rows = (db.query(FinPettyClaimItem.id, FinPettyClaimItem.truck_code, FinPettyClaimItem.event_date,
                     FinPettyClaimItem.claim_id)
            .join(FinPettyClaim, FinPettyClaim.id == FinPettyClaimItem.claim_id)
            .filter(FinPettyClaim.expense_type == claim.expense_type,
                    FinPettyClaim.status != L.RETURNED,
                    FinPettyClaimItem.event_date >= min(lows),
                    FinPettyClaimItem.event_date <= max(highs))
            .limit(5000).all())
    counters: dict[tuple, int] = {}
    out: dict[int, int] = {}
    for row in sorted(rows, key=lambda r: (r.event_date, r.claim_id, r.id)):
        code = L.normalize_code(row.truck_code)
        if code not in codes:
            continue
        key = (code, row.event_date.year, row.event_date.month)
        counters[key] = counters.get(key, 0) + 1
        out[row.id] = counters[key]
    return out


def employee_names(db: Session, employee_ids) -> dict[str, str]:
    """ชื่อ-สกุลของพนักงานหลายคนในคำสั่งเดียว — ใบพิมพ์และหน้าจอขึ้นชื่อ ไม่ใช่รหัส · ไม่พบ = ไม่มีคีย์"""
    from models.user_model import User
    wanted = sorted({str(i) for i in employee_ids if i})
    if not wanted:
        return {}
    rows = (db.query(User.employee_id, User.firstname, User.lastname)
            .filter(User.employee_id.in_(wanted)).all())
    out: dict[str, str] = {}
    for emp, first, last in rows:
        name = " ".join(part for part in (first, last) if part and str(part).strip()).strip()
        if emp and name:
            out[str(emp)] = name
    return out


def payee_account(db: Session, employee_id: Optional[str]) -> Optional[dict]:
    """บัญชีรับเงินที่ใช้อยู่ (ACTIVE) ของพนักงานจาก master ของ Advance · ไม่มี/ปิดอยู่ = None
    master มีแต่บัญชีธนาคาร (KBANK) จึงเป็นชนิด bank เสมอ"""
    if not employee_id:
        return None
    from models.finance_model import FinPayeeAccount
    row = (db.query(FinPayeeAccount)
           .filter(FinPayeeAccount.employee_id == employee_id, FinPayeeAccount.status == "ACTIVE")
           .first())
    if row is None:
        return None
    return {"payee_account_type": "bank", "bank": row.bank, "account_no": row.account_no,
            "account_name": row.account_name}


def employee_email(db: Session, employee_id: Optional[str]) -> Optional[str]:
    """อีเมลของพนักงานจาก users — ไม่มีก็คืน None แล้วให้ตัวส่งข้ามไป ไม่ใช่ส่งที่อยู่ว่าง"""
    if not employee_id:
        return None
    from models.user_model import User
    row = (db.query(User.email)
           .filter(User.employee_id == employee_id)
           .first())
    return row[0] if row and row[0] else None
