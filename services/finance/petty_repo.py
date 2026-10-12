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
