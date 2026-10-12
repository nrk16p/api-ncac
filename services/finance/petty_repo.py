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

from datetime import date as _date  # noqa: E402
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
            driver_name=raw.get("driver_name"),
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

from datetime import datetime, timezone  # noqa: E402

_NOW_FIELDS = {
    "APPROVED": "approved_at",
    "TRANSFERRED": "transfer_slip_at",
    "AWAITING_REVIEW": "submitted_to_acc_at",
}


def set_status(db: Session, claim: FinPettyClaim, target: str, actor: str, action: str,
               fields: Optional[dict] = None, remark: Optional[str] = None) -> FinPettyClaim:
    """จังหวะเดียวที่สถานะเปลี่ยนได้ — ทุก endpoint ต้องผ่านทางนี้ จึงมี log และกฎครบเสมอ"""
    L.assert_transition(claim.status, target)
    before = claim.status
    for key, value in (fields or {}).items():
        setattr(claim, key, value)
    stamp = _NOW_FIELDS.get(target)
    if stamp and getattr(claim, stamp) is None:
        setattr(claim, stamp, datetime.now(timezone.utc))
    if target in ("CLOSED", "SENT_BACK") and claim.reviewed_at is None:
        claim.reviewed_at, claim.reviewed_by = datetime.now(timezone.utc), actor
    claim.status = target
    changes = {"from": before, "to": target}
    changes.update({k: str(v) for k, v in (fields or {}).items()})
    log(db, claim.id, action, actor, changes=changes, remark=remark)
    db.commit()
    db.refresh(claim)
    return claim


_EDITABLE = ("PENDING_APPROVAL", "RETURNED")


def update_draft(db: Session, claim: FinPettyClaim, payload: dict, items: list[dict],
                 actor: str) -> FinPettyClaim:
    """แก้ได้เฉพาะตอนรออนุมัติหรือถูกตีกลับ · แก้แล้วกลับเข้าคิวอนุมัติเสมอ"""
    if claim.status not in _EDITABLE:
        raise L.PettyRuleError(
            f"แก้ไขไม่ได้ ใบนี้สถานะ {L.STATUS_LABELS.get(claim.status, claim.status)} แล้ว", 409)
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
# สถานะที่ถือเงินไว้ของ petty
COMMITTED_STATUSES = ("APPROVED", "TRANSFERRED", "AWAITING_REVIEW", "SENT_BACK")

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


def close_batch(db: Session, fund_id: int, claim_ids: list[int], actor: str,
                remark: Optional[str]) -> FinPettyBatch:
    """การเงินปิดรอบ: ใบที่บัญชีตรวจผ่านแล้ว → CLOSED ทีละรอบ แล้วยอดคืนเข้า available เอง"""
    fund = get_fund(db, fund_id)
    if fund is None:
        raise LookupError("ไม่พบกองทุน")
    claims = db.query(FinPettyClaim).filter(FinPettyClaim.id.in_(claim_ids)).all()
    if len(claims) != len(set(claim_ids)):
        raise L.PettyRuleError("มีใบที่หาไม่พบในรายการที่เลือก", 404)
    for claim in claims:
        if claim.fund_id != fund_id:
            raise L.PettyRuleError(f"ใบ {claim.form_id} ไม่ได้อยู่ในกองทุนนี้", 409)
        if claim.status != L.AWAITING_REVIEW:
            raise L.PettyRuleError(
                f"ใบ {claim.form_id} สถานะ {L.STATUS_LABELS.get(claim.status)} ยังปิดรอบไม่ได้", 409)

    yymm = int(datetime.now(timezone.utc).astimezone().strftime("%y%m"))
    used = (db.query(func.count(FinPettyBatch.id))
            .filter(FinPettyBatch.fund_id == fund_id,
                    FinPettyBatch.batch_no.like(f"%-{yymm:04d}-B%"))
            .scalar() or 0)
    batch = FinPettyBatch(
        fund_id=fund_id, closed_by=actor, remark=remark,
        batch_no=L.batch_no(fund.branch_code, fund.division_code, yymm, used + 1),
        total_amount=sum((c.amount for c in claims), _ZERO),
    )
    db.add(batch)
    db.flush()
    for claim in claims:
        set_status(db, claim, L.CLOSED, actor, "CLOSE", fields={"batch_id": batch.id})
    db.commit()
    db.refresh(batch)
    return batch


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

    rows = (db.query(FinPettyClaimItem, FinPettyClaim.form_id)
            .join(FinPettyClaim, FinPettyClaim.id == FinPettyClaimItem.claim_id)
            .filter(FinPettyClaim.expense_type == expense_type,
                    FinPettyClaim.status != L.RETURNED,
                    FinPettyClaimItem.event_date >= low,
                    FinPettyClaimItem.event_date <= high)
            .filter(FinPettyClaim.id != exclude_claim_id if exclude_claim_id else True)
            .limit(2000)  # ขอบเขตกันหลุด — ปกติได้ไม่กี่สิบแถว
            .all())

    out = []
    for item, form_id in rows:
        same_truck = (L.normalize_code(item.truck_code) in codes
                      or L.normalize_plate(item.truck_plate) in plates)
        same_ref = (item.ref_no or "").strip() in refs
        if not same_truck and not same_ref:
            continue
        out.append({"event_date": item.event_date, "truck_code": item.truck_code,
                    "truck_plate": item.truck_plate, "ref_no": item.ref_no, "form_id": form_id})
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


def employee_email(db: Session, employee_id: Optional[str]) -> Optional[str]:
    """อีเมลของพนักงานจาก users — ไม่มีก็คืน None แล้วให้ตัวส่งข้ามไป ไม่ใช่ส่งที่อยู่ว่าง"""
    if not employee_id:
        return None
    from models.user_model import User
    row = (db.query(User.email)
           .filter(User.employee_id == employee_id)
           .first())
    return row[0] if row and row[0] else None
