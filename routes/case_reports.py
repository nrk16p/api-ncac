from fastapi import APIRouter, Depends, HTTPException , Query
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
from database import get_db
from models import CaseReport, CaseProduct


router = APIRouter(prefix="/case_reports", tags=["Case Reports"])

SITE_CODES = {
    2: "LB",
    3: "SB",
    4: "SB",
    5: "LB",
    6: "BP",
}

def generate_document_no(db: Session, site_id: int) -> str:
    # Step 1: site code
    site_code = SITE_CODES.get(site_id, "XX")  # fallback if unknown

    # Step 2: all site_ids with the same site_code
    grouped_sites = [sid for sid, code in SITE_CODES.items() if code == site_code]

    # Step 3: current YYMM
    now = datetime.now()
    yymm = now.strftime("%y%m")

    # Step 4: start/end of current month
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if now.month == 12:
        next_month = now.replace(year=now.year + 1, month=1, day=1)
    else:
        next_month = now.replace(month=now.month + 1, day=1)

    # Step 5: count cases for THIS site_code group in this month
    count = (
        db.query(CaseReport)
        .filter(
            CaseReport.site_id.in_(grouped_sites),
            CaseReport.record_date >= start_of_month,
            CaseReport.record_date < next_month,
        )
        .count()
    )

    running = f"{count + 1:03d}"  # 001, 002, ...

    # Step 6: final format
    return f"NC-{site_code}-{yymm}-{running}"

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
        cp = CaseProduct(case_id=case_id, product_name=p.get("product_name"), amount=p.get("amount"), unit=p.get("unit"))
        db.add(cp)

class ProductSchema(BaseModel):
    product_name: str
    amount: Optional[int] = None
    unit: Optional[str] = None

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
    driver_id: Optional[int] = None
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
    casestatus: Optional[str] = "OPEN"
    products: Optional[List[ProductSchema]] = None

@router.post("/", status_code=201)
def create_or_update_case_report(payload: CaseReportSchema, db: Session = Depends(get_db)):
    document_no = payload.document_no or generate_document_no(db, payload.site_id)
    report = db.query(CaseReport).filter_by(document_no=document_no).first()

    if report:
        data = payload.dict(exclude_unset=True, exclude={"products"})
        for key, value in data.items():
            if key in ["record_date", "incident_date"]:
                setattr(report, key, parse_dt(value))
            else:
                setattr(report, key, value)
        if payload.products is not None:
            upsert_products(db, report.case_id, [p.dict() for p in payload.products])
        db.commit()
        return {"message": "Case report updated", "document_no": document_no}

    report = CaseReport(
        document_no=document_no,
        site_id=payload.site_id,
        department_id=payload.department_id,
        client_id=payload.client_id,
        vehicle_id_head=payload.vehicle_id_head,
        vehicle_id_tail=payload.vehicle_id_tail,
       vehicle_truckno=payload.vehicle_truckno,   # <-- this one fails

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
        casestatus=payload.casestatus or "สร้างเอกสาร",
    )
    db.add(report)
    db.flush()
    if payload.products:
        upsert_products(db, report.case_id, [p.dict() for p in payload.products])
    db.commit()
    return {"message": "Case report created", "document_no": document_no}




@router.get("/")
def get_case_reports(
    db: Session = Depends(get_db),
    document_no: Optional[List[str]] = Query(None),
    site_id: Optional[List[int]] = Query(None),
    driver_id: Optional[List[int]] = Query(None),
    casestatus: Optional[List[str]] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None)
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
    if start_date and end_date:
        start = parse_dt(start_date)
        end = parse_dt(end_date)
        if start and end:
            query = query.filter(CaseReport.record_date.between(start, end))

    reports = query.order_by(CaseReport.case_id.desc()).all()
    return [r.to_dict() for r in reports]


@router.get("/{document_no}")
def get_case_report(document_no: str, db: Session = Depends(get_db)):
    r = db.query(CaseReport).filter(CaseReport.document_no == document_no).first()
    if not r:
        raise HTTPException(status_code=404, detail="Case report not found")
    return r.to_dict()

@router.put("/{case_id}")
def update_case_report(case_id: int, payload: CaseReportSchema, db: Session = Depends(get_db)):
    report = db.query(CaseReport).get(case_id)
    if not report:
        raise HTTPException(status_code=404, detail="Case report not found")
    data = payload.dict(exclude_unset=True, exclude={"products"})
    for key, value in data.items():
        if key in ["record_date", "incident_date"]:
            setattr(report, key, parse_dt(value))
        else:
            setattr(report, key, value)
    if payload.products is not None:
        upsert_products(db, report.case_id, [p.dict() for p in payload.products])
    db.commit()
    return {"message": "Case report updated"}

@router.delete("/{case_id}")
def delete_case_report(case_id: int, db: Session = Depends(get_db)):
    report = db.query(CaseReport).get(case_id)
    if not report:
        raise HTTPException(status_code=404, detail="Case report not found")
    db.delete(report)
    db.commit()
    return {"message": "Case report deleted"}
