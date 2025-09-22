from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
from database import get_db
from models import CaseReport, CaseProduct

router = APIRouter(prefix="/case_reports", tags=["Case Reports"])

def generate_document_no(db: Session) -> str:
    last = db.query(CaseReport).order_by(CaseReport.case_id.desc()).first()
    if last and last.document_no and last.document_no.startswith("CR-"):
        try:
            last_num = int(last.document_no.split("-")[1])
        except Exception:
            last_num = 0
        return f"CR-{last_num + 1:03d}"
    return "CR-001"

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
    origin_id: Optional[int] = None
    driver_role_id: Optional[int] = None
    driver_id: Optional[int] = None
    incident_cause_id: Optional[int] = None
    employee_id: Optional[int] = None
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
    document_no = payload.document_no or generate_document_no(db)
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
        origin_id=payload.origin_id,
        driver_role_id=payload.driver_role_id,
        driver_id=payload.driver_id,
        incident_cause_id=payload.incident_cause_id,
        employee_id=payload.employee_id,
        reporter_id=payload.reporter_id,
        record_date=parse_dt(payload.record_date),
        incident_date=parse_dt(payload.incident_date),
        case_location=payload.case_location,
        destination=payload.destination,
        case_details=payload.case_details,
        estimated_cost=payload.estimated_cost,
        actual_price=payload.actual_price,
        attachments=payload.attachments,
        casestatus=payload.casestatus or "OPEN",
    )
    db.add(report)
    db.flush()
    if payload.products:
        upsert_products(db, report.case_id, [p.dict() for p in payload.products])
    db.commit()
    return {"message": "Case report created", "document_no": document_no}

@router.get("/")
def get_case_reports(db: Session = Depends(get_db)):
    reports = db.query(CaseReport).order_by(CaseReport.case_id.desc()).all()
    return [r.to_dict() for r in reports]

@router.get("/{case_id}")
def get_case_report(case_id: int, db: Session = Depends(get_db)):
    r = db.query(CaseReport).get(case_id)
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
