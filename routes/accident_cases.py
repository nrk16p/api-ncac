from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from database import get_db
import models, schemas
from schemas.accident_schema import AccidentCaseDocData

router = APIRouter(prefix="/accident-cases", tags=["Accident Cases"])

# -----------------------------
# Static site mapping
# -----------------------------
SITE_CODES = {
    1: "SB",
    2: "LB",
    3: "SB",
    4: "SB",
    5: "LB",
    6: "BP",
}

# -----------------------------
# Generate unique document number
# -----------------------------
def generate_document_no_ac(db: Session, site_id: int) -> str:
    site_code = SITE_CODES.get(site_id, "XX")
    grouped_sites = [sid for sid, code in SITE_CODES.items() if code == site_code]
    now = datetime.now(timezone.utc)
    yymm = now.strftime("%y%m")

    prefix = f"AC-{site_code}-{yymm}-"

    last = (
        db.query(models.AccidentCase.document_no_ac)
        .filter(models.AccidentCase.document_no_ac.like(f"{prefix}%"))
        .order_by(models.AccidentCase.document_no_ac.desc())
        .first()
    )
    if last and last[0]:
        last_num = int(last[0].split("-")[-1])
    else:
        last_num = 0
    new_num = last_num + 1
    return f"{prefix}{new_num:03d}"

# -----------------------------
# Calculate Priority
# -----------------------------
def calculate_priority(
    estimated_goods_damage_value: Optional[float],
    estimated_vehicle_damage_value: Optional[float],
    actual_goods_damage_value: Optional[float],
    actual_vehicle_damage_value: Optional[float],
    alcohol_test_result: Optional[float],
    drug_test_result: Optional[str],
    injured_not_hospitalized: Optional[int],
    injured_hospitalized: Optional[int],
    fatalities: Optional[int]
) -> Optional[str]:
    estimate = (estimated_goods_damage_value or 0) + (estimated_vehicle_damage_value or 0)
    actual = (actual_goods_damage_value or 0) + (actual_vehicle_damage_value or 0)
    total_damage = actual if actual else estimate

    alcohol_test_result = alcohol_test_result or 0
    drug_test_result = (drug_test_result or "").strip().lower()
    injured_not_hospitalized = injured_not_hospitalized or 0
    injured_hospitalized = injured_hospitalized or 0
    fatalities = fatalities or 0

    # 🧠 NEW SAFE VALUES
    safe_values = ["", "none", "negative", "ไม่ใส่ชนิดสารเสพติด"]

    if (
        total_damage > 500000
        or alcohol_test_result > 0
        or (drug_test_result and drug_test_result not in safe_values)
        or fatalities >= 1
    ):
        return "Crisis"
    if 5001 <= total_damage <= 500000 and fatalities == 0 or injured_hospitalized >= 1:
        return "Major"
    if (total_damage <= 5000) or (injured_not_hospitalized >= 1 and fatalities == 0):
        return "Minor"
    return "Minor"

# -----------------------------
# Create Case (with optional docs)
# -----------------------------
@router.post("", response_model=schemas.AccidentCaseResponse, status_code=201)
def create_case(payload: dict, db: Session = Depends(get_db)):
    """Create accident case + optional docs section"""
    case_data = schemas.AccidentCaseCreate(**payload)
    docs_data = payload.get("docs")

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

    # ✅ Create docs separately
    docs_data = case_data.docs or []
    for doc in docs_data:
        doc_record = models.AccidentCaseDoc(document_no_ac=doc_no, data=doc)
        db.add(doc_record)

    db.commit()

    db.refresh(case)
    return case.to_dict()

# -----------------------------
# Helper: Parse datetime
# -----------------------------
def parse_dt(val):
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(val)
    except Exception:
        return None

# -----------------------------
# Get Cases with Filters
# -----------------------------
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
    query = (
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
        start = parse_dt(start_date)
        end = parse_dt(end_date)
        if start and end:
            query = query.filter(
                models.AccidentCase.record_datetime >= start,
                models.AccidentCase.record_datetime < end + timedelta(days=1),
            )

    cases = query.order_by(models.AccidentCase.record_datetime.desc()).all()
    return [case.to_dict() for case in cases]

# -----------------------------
# Update Case by document_no_ac
# -----------------------------
@router.put("/{document_no_ac}", response_model=schemas.AccidentCaseResponse)
def update_case(document_no_ac: str, payload: schemas.AccidentCaseUpdate, db: Session = Depends(get_db)):
    # 🔍 1. Fetch the case
    case = (
        db.query(models.AccidentCase)
        .filter(models.AccidentCase.document_no_ac == document_no_ac)
        .first()
    )

    if not case:
        raise HTTPException(status_code=404, detail=f"Case '{document_no_ac}' not found")

    # 🧱 2. Apply updates dynamically
    update_data = payload.dict(exclude_unset=True, exclude={"priority", "document_no_ac"})
    for key, value in update_data.items():
        # Convert 0 → None for *_id fields
        if key.endswith("_id") and value == 0:
            value = None
        setattr(case, key, value)

    # 🧠 3. Flush changes to make them visible to SQLAlchemy
    db.flush()

    # ⚖️ 4. Recalculate priority using updated data
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

    # 💾 5. Commit & refresh
    try:
        db.commit()
        db.refresh(case)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Database error: {str(e)}")

    # ✅ 6. Return updated dict
    return case.to_dict()

# -----------------------------
# Delete Case
# -----------------------------
@router.delete("/{case_id}", status_code=204)
def delete_case(case_id: int, db: Session = Depends(get_db)):
    case = db.query(models.AccidentCase).get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    db.delete(case)
    db.commit()

# -------------------------------------------------------------------
# ⭐ NEW — CREATE / UPDATE AccidentCaseDoc USING document_no_ac
# -------------------------------------------------------------------
@router.post("/{document_no_ac}/docs", response_model=schemas.AccidentCaseDocSchema)
def add_accident_case_doc(document_no_ac: str, payload: schemas.AccidentCaseDocData, db: Session = Depends(get_db)):
    case = (
        db.query(models.AccidentCase)
        .filter(models.AccidentCase.document_no_ac == document_no_ac)
        .first()
    )

    if not case:
        raise HTTPException(status_code=404, detail="Accident case not found")

    doc = models.AccidentCaseDoc(
        document_no_ac=document_no_ac,
        data=payload.dict()
    )

    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc

# -------------------------------------------------------------------
# ⭐ NEW — GET all docs for a case
# -------------------------------------------------------------------
@router.get("/{document_no_ac}/docs", response_model=List[schemas.AccidentCaseDocSchema])
def get_docs(document_no_ac: str, db: Session = Depends(get_db)):
    docs = (
        db.query(models.AccidentCaseDoc)
        .filter(models.AccidentCaseDoc.document_no_ac == document_no_ac)
        .all()
    )
    return docs

# -------------------------------------------------------------------
# ⭐ NEW — DELETE a doc
# -------------------------------------------------------------------
@router.delete("/{document_no_ac}/docs/{doc_id}", status_code=204)
def delete_doc(document_no_ac: str, doc_id: int, db: Session = Depends(get_db)):
    doc = (
        db.query(models.AccidentCaseDoc)
        .filter(
            models.AccidentCaseDoc.document_no_ac == document_no_ac,
            models.AccidentCaseDoc.id == doc_id,
        )
        .first()
    )

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    db.delete(doc)
    db.commit()


# -------------------------------------------------------------------
# ⭐ NEW — GET single accident case by document_no_ac
# -------------------------------------------------------------------
@router.get("/{document_no_ac}", response_model=schemas.AccidentCaseResponse)
def get_accident_case(document_no_ac: str, db: Session = Depends(get_db)):
    """Get one accident case with related nested docs"""
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
