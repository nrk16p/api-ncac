from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from database import get_db
import models, schemas
from schemas.accident_schema import AccidentCaseDocData

router = APIRouter(prefix="/accident-cases", tags=["Accident Cases"])

# -----------------------------------------------------
# SITE CODE MAPPING
# -----------------------------------------------------
SITE_CODES = {
    1: "SB",
    2: "LB",
    3: "SB",
    4: "SB",
    5: "LB",
    6: "BP",
}

# -----------------------------------------------------
# DOCUMENT NUMBER GENERATOR
# -----------------------------------------------------
def generate_document_no_ac(db: Session, site_id: int) -> str:
    site_code = SITE_CODES.get(site_id, "XX")
    now = datetime.now(timezone.utc)
    yymm = now.strftime("%y%m")
    prefix = f"AC-{site_code}-{yymm}-"

    last = (
        db.query(models.AccidentCase.document_no_ac)
        .filter(models.AccidentCase.document_no_ac.like(f"{prefix}%"))
        .order_by(models.AccidentCase.document_no_ac.desc())
        .first()
    )

    last_num = int(last[0].split("-")[-1]) if last and last[0] else 0
    return f"{prefix}{last_num + 1:03d}"

# -----------------------------------------------------
# PRIORITY CALCULATION
# -----------------------------------------------------
def calculate_priority(
    estimated_goods_damage_value: Optional[float],
    estimated_vehicle_damage_value: Optional[float],
    actual_goods_damage_value: Optional[float],
    actual_vehicle_damage_value: Optional[float],
    alcohol_test_result: Optional[float],
    drug_test_result: Optional[str],
    injured_not_hospitalized: Optional[int],
    injured_hospitalized: Optional[int],
    fatalities: Optional[int],
) -> str:
    estimate = (estimated_goods_damage_value or 0) + (estimated_vehicle_damage_value or 0)
    actual = (actual_goods_damage_value or 0) + (actual_vehicle_damage_value or 0)
    total_damage = actual if actual else estimate

    alcohol_test_result = alcohol_test_result or 0
    drug_test_result = (drug_test_result or "").strip().lower()
    injured_not_hospitalized = injured_not_hospitalized or 0
    injured_hospitalized = injured_hospitalized or 0
    fatalities = fatalities or 0

    safe_drug_values = {"", "none", "negative", "ไม่ใส่ชนิดสารเสพติด"}

    # --- Priority Logic ---
    if (
        total_damage > 500000
        or alcohol_test_result > 0
        or (drug_test_result and drug_test_result not in safe_drug_values)
        or fatalities >= 1
    ):
        return "Crisis"
    elif 5001 <= total_damage <= 500000 and fatalities == 0 or (injured_hospitalized >= 1):
        return "Major"
    elif (total_damage <= 5000) or (injured_not_hospitalized >= 1 and fatalities == 0):
        return "Minor"
    else:
        return "Minor"

# -----------------------------------------------------
# CREATE CASE
# -----------------------------------------------------
@router.post("", response_model=schemas.AccidentCaseResponse, status_code=201)
def create_case(payload: dict, db: Session = Depends(get_db)):
    case_data = schemas.AccidentCaseCreate(**payload)
    doc_no = generate_document_no_ac(db, case_data.site_id)

    priority = calculate_priority(
        case_data.estimated_goods_damage_value,
        case_data.estimated_vehicle_damage_value,
        case_data.actual_goods_damage_value,
        case_data.actual_vehicle_damage_value,
        case_data.alcohol_test_result,
        case_data.drug_test_result,
        case_data.injured_not_hospitalized,
        case_data.injured_hospitalized,
        case_data.fatalities,
    )

    case = models.AccidentCase(
        **case_data.dict(exclude={"priority", "document_no_ac", "casestatus", "attachments", "docs"}),
        document_no_ac=doc_no,
        priority=priority,
        casestatus="Pending",
        attachments=f"https://mena-safety-ncac.vercel.app/nc-form?doc={doc_no}",
    )

    db.add(case)
    db.commit()
    db.refresh(case)

    # Create documents if any
    for doc in case_data.docs or []:
        db.add(models.AccidentCaseDoc(document_no_ac=doc_no, data=doc))
    db.commit()

    return case.to_dict()

# -----------------------------------------------------
# DATETIME PARSER
# -----------------------------------------------------
def parse_dt(val):
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(val)
    except Exception:
        return None

# -----------------------------------------------------
# LIST CASES (FILTERABLE)
# -----------------------------------------------------
@router.get("", response_model=List[schemas.AccidentCaseResponse])
def get_accident_cases(
    db: Session = Depends(get_db),
    document_no_ac: Optional[List[str]] = Query(None),
    site_id: Optional[List[int]] = Query(None),
    priority: Optional[List[str]] = Query(None),
    driver_id: Optional[List[str]] = Query(None),
    casestatus: Optional[List[str]] = Query(None),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    query = db.query(models.AccidentCase).options(
        joinedload(models.AccidentCase.site),
        joinedload(models.AccidentCase.department),
        joinedload(models.AccidentCase.client),
        joinedload(models.AccidentCase.origin),
        joinedload(models.AccidentCase.reporter),
        joinedload(models.AccidentCase.driver),
        joinedload(models.AccidentCase.driver_role),
        joinedload(models.AccidentCase.vehicle_head),
        joinedload(models.AccidentCase.vehicle_tail),
        joinedload(models.AccidentCase.province),
        joinedload(models.AccidentCase.district),
        joinedload(models.AccidentCase.sub_district),
        joinedload(models.AccidentCase.docs),
    )

    if document_no_ac:
        query = query.filter(models.AccidentCase.document_no_ac.in_(document_no_ac))
    if site_id:
        query = query.filter(models.AccidentCase.site_id.in_(site_id))
    if priority:
        query = query.filter(models.AccidentCase.priority.in_(priority))
    if driver_id:
        query = query.filter(models.AccidentCase.driver_id.in_(driver_id))
    if casestatus:
        query = query.filter(models.AccidentCase.casestatus.in_(casestatus))
    if start_date and end_date:
        start, end = parse_dt(start_date), parse_dt(end_date)
        if start and end:
            query = query.filter(
                models.AccidentCase.record_datetime >= start,
                models.AccidentCase.record_datetime < end + timedelta(days=1),
            )

    cases = query.order_by(models.AccidentCase.record_datetime.desc()).all()
    return [case.to_dict() for case in cases]

# -----------------------------------------------------
# UPDATE CASE
# -----------------------------------------------------
@router.put("/{document_no_ac}", response_model=schemas.AccidentCaseResponse)
def update_case(document_no_ac: str, payload: dict, db: Session = Depends(get_db)):
    """
    Update a case by document_no_ac, safely handling both normal fields and docs.
    """
    case = (
        db.query(models.AccidentCase)
        .filter(models.AccidentCase.document_no_ac == document_no_ac)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail=f"Case '{document_no_ac}' not found")

    # ✅ Handle docs separately
    docs_data = payload.get("docs", None)
    if docs_data:
        # Delete existing docs for this case
        db.query(models.AccidentCaseDoc).filter(
            models.AccidentCaseDoc.document_no_ac == document_no_ac
        ).delete(synchronize_session=False)

        # Insert new docs
        for doc in docs_data:
            new_doc = models.AccidentCaseDoc(
                document_no_ac=document_no_ac,
                data=doc
            )
            db.add(new_doc)

    # ✅ Update other fields
    update_fields = {
        k: v for k, v in payload.items()
        if k not in {"priority", "document_no_ac", "docs"}
    }

    for key, value in update_fields.items():
        if key.endswith("_id") and value == 0:
            value = None
        setattr(case, key, value)

    db.flush()

    # ✅ Recalculate priority
    case.priority = calculate_priority(
        case.estimated_goods_damage_value,
        case.estimated_vehicle_damage_value,
        case.actual_goods_damage_value,
        case.actual_vehicle_damage_value,
        case.alcohol_test_result,
        case.drug_test_result,
        case.injured_not_hospitalized,
        case.injured_hospitalized,
        case.fatalities,
    )

    try:
        db.commit()
        db.refresh(case)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Database error: {str(e)}")

    return case.to_dict()

# -----------------------------------------------------
# DELETE CASE
# -----------------------------------------------------
@router.delete("/{case_id}", status_code=204)
def delete_case(case_id: int, db: Session = Depends(get_db)):
    case = db.get(models.AccidentCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    db.delete(case)
    db.commit()

# -----------------------------------------------------
# DOCS MANAGEMENT
# -----------------------------------------------------
@router.post("/{document_no_ac}/docs", response_model=schemas.AccidentCaseDocSchema)
def add_accident_case_doc(document_no_ac: str, payload: schemas.AccidentCaseDocData, db: Session = Depends(get_db)):
    case = db.query(models.AccidentCase).filter(models.AccidentCase.document_no_ac == document_no_ac).first()
    if not case:
        raise HTTPException(status_code=404, detail="Accident case not found")

    doc = models.AccidentCaseDoc(document_no_ac=document_no_ac, data=payload.dict())
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc

@router.get("/{document_no_ac}/docs", response_model=List[schemas.AccidentCaseDocSchema])
def get_docs(document_no_ac: str, db: Session = Depends(get_db)):
    return db.query(models.AccidentCaseDoc).filter(models.AccidentCaseDoc.document_no_ac == document_no_ac).all()

@router.delete("/{document_no_ac}/docs/{doc_id}", status_code=204)
def delete_doc(document_no_ac: str, doc_id: int, db: Session = Depends(get_db)):
    doc = db.query(models.AccidentCaseDoc).filter(
        models.AccidentCaseDoc.document_no_ac == document_no_ac,
        models.AccidentCaseDoc.id == doc_id
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    db.delete(doc)
    db.commit()

# -----------------------------------------------------
# GET SINGLE CASE
# -----------------------------------------------------
@router.get("/{document_no_ac}", response_model=schemas.AccidentCaseResponse)
def get_accident_case(document_no_ac: str, db: Session = Depends(get_db)):
    case = (
        db.query(models.AccidentCase)
        .options(
            joinedload(models.AccidentCase.site),
            joinedload(models.AccidentCase.department),
            joinedload(models.AccidentCase.client),
            joinedload(models.AccidentCase.origin),
            joinedload(models.AccidentCase.reporter),
            joinedload(models.AccidentCase.driver),
            joinedload(models.AccidentCase.driver_role),
            joinedload(models.AccidentCase.vehicle_head),
            joinedload(models.AccidentCase.vehicle_tail),
            joinedload(models.AccidentCase.province),
            joinedload(models.AccidentCase.district),
            joinedload(models.AccidentCase.sub_district),
            joinedload(models.AccidentCase.docs),
        )
        .filter(models.AccidentCase.document_no_ac == document_no_ac)
        .first()
    )

    if not case:
        raise HTTPException(status_code=404, detail=f"Case '{document_no_ac}' not found")

    return case.to_dict()
