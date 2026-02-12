from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List, Optional
from pydantic import BaseModel

from database import get_db
from models import CaseReport, CaseProduct, CaseReportDoc   # 👈 ADDED IMPORT

router = APIRouter(prefix="/case_reports", tags=["Case Reports"])

# ============================================================
# Helpers
# ============================================================

SITE_CODES = {
    2: "LB",
    3: "SB",
    4: "SB",
    5: "LB",
    6: "BP",
}

def generate_document_no(db: Session, site_id: int) -> str:
    site_code = SITE_CODES.get(site_id, "XX")
    grouped_sites = [sid for sid, code in SITE_CODES.items() if code == site_code]

    now = datetime.now()
    yymm = now.strftime("%y%m")

    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = (
        now.replace(year=now.year + 1, month=1, day=1)
        if now.month == 12 else
        now.replace(month=now.month + 1, day=1)
    )

    count = (
        db.query(CaseReport)
        .filter(
            CaseReport.site_id.in_(grouped_sites),
            CaseReport.record_date >= start_of_month,
            CaseReport.record_date < next_month,
        )
        .count()
    )
    return f"NC-{site_code}-{yymm}-{count+1:03d}"


def parse_dt(val):
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(val)
    except Exception:
        return None


def upsert_products(db: Session, case_id: int, products_payload: Optional[List[dict]]):
    db.query(CaseProduct).filter_by(case_id=case_id).delete()
    for p in products_payload or []:
        db.add(CaseProduct(
            case_id=case_id,
            product_name=p.get("product_name"),
            amount=p.get("amount"),
            unit=p.get("unit"),
        ))


def upsert_docs(db: Session, case_id: int, docs_payload: Optional[List[dict]]):
    db.query(CaseReportDoc).filter_by(case_id=case_id).delete()
    for item in docs_payload or []:
        db.add(CaseReportDoc(
            case_id=case_id,
            data=item   # JSON stored directly
        ))

# ============================================================
# Schemas
# ============================================================

class ProductSchema(BaseModel):
    product_name: str
    amount: Optional[float] = None
    unit: Optional[str] = None


# 👇 ADDED CORRECT DocItem schema
class DocItem(BaseModel):
    # 🧾 Existing document types
    warning_doc: Optional[str] = None
    warning_doc_no: Optional[str] = None
    warning_doc_remark: Optional[str] = None

    debt_doc: Optional[str] = None
    debt_doc_no: Optional[str] = None
    debt_doc_remark: Optional[str] = None

    customer_invoice: Optional[str] = None
    customer_invoice_no: Optional[str] = None
    customer_invoice_remark: Optional[str] = None

    Insurance_claim_doc: Optional[str] = None
    Insurance_claim_doc_no: Optional[str] = None
    Insurance_claim_doc_remark: Optional[str] = None

    writeoff_doc: Optional[str] = None
    writeoff_doc_remark: Optional[str] = None

    damage_payment: Optional[str] = None
    damage_payment_no: Optional[str] = None
    damage_payment_remark: Optional[str] = None

    account_attachment_no: Optional[str] = None
    account_attachment_remark: Optional[str] = None

    # 🆕 Added fields from your new payload
    event_img: Optional[str] = None
    event_img_remark: Optional[str] = None

    account_attachment_sell: Optional[str] = None
    account_attachment_sell_remark: Optional[str] = None

    account_attachment_insurance: Optional[str] = None
    account_attachment_insurance_remark: Optional[str] = None

    account_attachment_pjs_pay: Optional[str] = None
    account_attachment_pjs_pay_remark: Optional[str] = None

    account_attachment_company_pay: Optional[str] = None
    account_attachment_company_pay_remark: Optional[str] = None

    class Config:
        extra = "allow"   # ✅ Allow future dynamic fields safely


class CaseReportSchema(BaseModel):
    document_no: Optional[str] = None
    site_id: Optional[int] = None
    department_id: Optional[int] = None
    client_id: Optional[int] = None
    vehicle_id_head: Optional[int] = None
    vehicle_id_tail: Optional[int] = None
    vehicle_truckno: Optional[str] = None
    origin_id: Optional[int] = None
    driver_role_id: Optional[int] = None
    driver_id: Optional[str] = None
    incident_cause_id: Optional[int] = None
    reporter_id: Optional[int] = None
    record_date: Optional[str] = None
    incident_date: Optional[str] = None
    case_location: Optional[str] = None
    destination: Optional[str] = None
    case_details: Optional[str] = None
    estimated_cost: Optional[float] = None
    actual_price: Optional[float] = None
    attachments: Optional[str] = None
    casestatus: Optional[str] = "Pending"

    products: Optional[List[ProductSchema]] = None

    priority: Optional[str] = None        # 👈 FIXED (was List)
    docs: Optional[List[DocItem]] = None  # 👈 FIXED

# ============================================================
# Priority Calculator
# ============================================================

def calculate_priority(estimated_cost: float, actual_price: float) -> Optional[str]:
    value = actual_price if actual_price not in (None, 0) else estimated_cost
    if value in (None, 0):
        return "Minor"
    if value < 5000:
        return "Minor"
    if 5000 <= value <= 50000:
        return "Major"
    return "Crisis"

# ============================================================
# Routes
# ============================================================

@router.post("", status_code=201)
def create_or_update_case_report(payload: CaseReportSchema, db: Session = Depends(get_db)):
    document_no = payload.document_no or generate_document_no(db, payload.site_id)
    report = db.query(CaseReport).filter_by(document_no=document_no).first()

    if report:
        data = payload.dict(exclude_unset=True, exclude={"products", "docs"})
        for key, value in data.items():
            if key in ["record_date", "incident_date"]:
                setattr(report, key, parse_dt(value))
            else:
                setattr(report, key, value)

        report.priority = calculate_priority(report.estimated_cost, report.actual_price)

        if payload.products is not None:
            upsert_products(db, report.case_id, [p.dict() for p in payload.products])
        if payload.docs is not None:
            upsert_docs(db, report.case_id, [d.dict() for d in payload.docs])

        db.commit()
        return {"message": "Case report updated", "document_no": document_no, "priority": report.priority}

    # Create
    report = CaseReport(
        document_no=document_no,
        site_id=payload.site_id,
        department_id=payload.department_id,
        client_id=payload.client_id,
        vehicle_id_head=payload.vehicle_id_head,
        vehicle_id_tail=payload.vehicle_id_tail,
        vehicle_truckno=payload.vehicle_truckno,
        origin_id=payload.origin_id,
        driver_role_id=payload.driver_role_id,
        driver_id=payload.driver_id,
        incident_cause_id=payload.incident_cause_id,
        reporter_id=payload.reporter_id,
        record_date=parse_dt(payload.record_date),
        incident_date=parse_dt(payload.incident_date),
        case_location=payload.case_location,
        destination=payload.destination,
        case_details=payload.case_details,
        estimated_cost=payload.estimated_cost,
        actual_price=payload.actual_price,
        attachments=payload.attachments,
        casestatus=payload.casestatus or "Pending",
    )

    report.priority = calculate_priority(payload.estimated_cost, payload.actual_price)

    db.add(report)
    db.flush()

    if payload.products:
        upsert_products(db, report.case_id, [p.dict() for p in payload.products])
    if payload.docs:
        upsert_docs(db, report.case_id, [d.dict() for d in payload.docs])

    db.commit()

    return {"message": "Case report created", "document_no": document_no, "priority": report.priority}


@router.get("/{document_no}")
def get_case_report(document_no: str, db: Session = Depends(get_db)):
    r = db.query(CaseReport).filter_by(document_no=document_no).first()
    if not r:
        raise HTTPException(status_code=404, detail="Case report not found")
    return r.to_dict()
@router.get("")
def get_case_reports(
    db: Session = Depends(get_db),
    document_no: Optional[List[str]] = Query(None),
    site_id: Optional[List[int]] = Query(None),
    driver_id: Optional[List[str]] = Query(None),
    casestatus: Optional[List[str]] = Query(None),
    priority: Optional[List[str]] = Query(None),
    client_id: Optional[List[int]] = Query(None),          # ✅ NEW
    department_id: Optional[List[int]] = Query(None),      # ✅ NEW
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    query = db.query(CaseReport)

    if document_no:
        query = query.filter(CaseReport.document_no.in_(document_no))

    if site_id:
        query = query.filter(CaseReport.site_id.in_(site_id))

    if driver_id:
        query = query.filter(CaseReport.driver_id.in_(driver_id))

    if casestatus:
        query = query.filter(CaseReport.casestatus.in_(casestatus))

    if priority:
        query = query.filter(CaseReport.priority.in_(priority))

    # ✅ NEW FILTERS
    if client_id:
        query = query.filter(CaseReport.client_id.in_(client_id))

    if department_id:
        query = query.filter(CaseReport.department_id.in_(department_id))

    if start_date and end_date:
        start = parse_dt(start_date)
        end = parse_dt(end_date)
        if start and end:
            end_next = end + timedelta(days=1)
            query = query.filter(
                CaseReport.record_date >= start,
                CaseReport.record_date < end_next
            )

    reports = query.order_by(CaseReport.case_id.desc()).all()
    return [r.to_dict() for r in reports]

@router.put("/{document_no}")
def update_case_report(document_no: str, payload: CaseReportSchema, db: Session = Depends(get_db)):
    report = db.query(CaseReport).filter_by(document_no=document_no).first()
    if not report:
        raise HTTPException(status_code=404, detail="Case report not found")

    data = payload.dict(exclude_unset=True, exclude={"products", "docs"})
    for key, value in data.items():
        if key in ["record_date", "incident_date"]:
            setattr(report, key, parse_dt(value))
        else:
            setattr(report, key, value)

    report.priority = calculate_priority(report.estimated_cost, report.actual_price)

    if payload.products is not None:
        upsert_products(db, report.case_id, [p.dict() for p in payload.products])
    if payload.docs is not None:
        upsert_docs(db, report.case_id, [d.dict() for d in payload.docs])

    db.commit()
    return {"message": "Case report updated", "document_no": document_no, "priority": report.priority}

