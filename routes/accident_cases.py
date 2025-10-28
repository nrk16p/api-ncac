from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from database import get_db
import models, schemas

router = APIRouter(prefix="/accident-cases", tags=["Accident Cases"])

# -----------------------------
# Static site mapping (keep using this)
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
# Generate Document Number
# -----------------------------
def generate_document_no_ac(db: Session, site_id: int) -> str:
    """Generate a unique accident case document number based on SITE_CODES mapping."""
    site_code = SITE_CODES.get(site_id, "XX")

    # All site_ids with the same code (LB, SB, BP)
    grouped_sites = [sid for sid, code in SITE_CODES.items() if code == site_code]

    # Current month in YYMM format
    now = datetime.now(timezone.utc)
    yymm = now.strftime("%y%m")

    # Define start and end of month
    start_of_month = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    if now.month == 12:
        next_month = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_month = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)

    # Count existing records in this site group and month
    count = (
        db.query(models.AccidentCase)
        .filter(
            models.AccidentCase.site_id.in_(grouped_sites),
            models.AccidentCase.record_datetime >= start_of_month,
            models.AccidentCase.record_datetime < next_month,
        )
        .count()
    )

    running = f"{count + 1:03d}"  # 001, 002, ...
    return f"AC-{site_code}-{yymm}-{running}"


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

    if (total_damage > 500000 or alcohol_test_result > 0 or
        (drug_test_result and drug_test_result not in ["", "none", "ไม่ใส่ชนิดสารเสพติด"]) or
        fatalities >= 1):
        return "Crisis"
    if (50001 <= total_damage <= 500000 and fatalities == 0):
        return "Major"
    if (5001 <= total_damage <= 50000 or injured_hospitalized >= 1 and fatalities == 0):
        return "Significant"
    if (total_damage <= 5000 or injured_not_hospitalized >= 1 and fatalities == 0):
        return "Minor"
    return "Minor"
## update

# -----------------------------
# Create Case
# -----------------------------
@router.post("/", response_model=schemas.AccidentCaseResponse, status_code=201)
def create_case(payload: schemas.AccidentCaseCreate, db: Session = Depends(get_db)):
    """Create a new accident case with auto-generated document number and priority."""
    doc_no = generate_document_no_ac(db, payload.site_id)

    priority = calculate_priority(
        payload.estimated_goods_damage_value,
        payload.estimated_vehicle_damage_value,
        payload.actual_goods_damage_value,
        payload.actual_vehicle_damage_value,
        payload.alcohol_test_result,
        payload.drug_test_result,
        payload.injured_not_hospitalized,
        payload.injured_hospitalized,
        payload.fatalities,
    )

    case = models.AccidentCase(
        **payload.dict(exclude={"priority", "document_no_ac", "casestatus", "attachments"}),
        document_no_ac=doc_no,
        priority=priority,
        casestatus="OPEN",
        attachments=f"https://mena-safety-ncac.vercel.app/nc-form?doc={doc_no}",
    )

    db.add(case)
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Database error: {str(e)}")

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
@router.get("/", response_model=List[schemas.AccidentCaseResponse])
def get_accident_cases(
    db: Session = Depends(get_db),
    document_no_ac: Optional[List[str]] = Query(None, description="Filter by document number(s)"),
    site_id: Optional[List[int]] = Query(None, description="Filter by site ID(s)"),
    priority: Optional[List[str]] = Query(None, description="Filter by priority(ies)"),
    driver_id: Optional[List[str]] = Query(None, description="Filter by driver ID(s)"),
    casestatus: Optional[List[str]] = Query(None, description="Filter by case status(es)"),
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

#AC
# -----------------------------
# Update Case
# -----------------------------
# -----------------------------
# Update Case by document_no_ac
# -----------------------------
@router.put("/{document_no_ac}", response_model=schemas.AccidentCaseResponse)
def update_case(document_no_ac: str, payload: schemas.AccidentCaseUpdate, db: Session = Depends(get_db)):
    """Update accident case using document_no_ac instead of numeric ID."""
    case = (
        db.query(models.AccidentCase)
        .filter(models.AccidentCase.document_no_ac == document_no_ac)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    # ✅ Update only fields provided in payload
    for key, value in payload.dict(exclude_unset=True, exclude={"priority", "document_no_ac"}).items():
        setattr(case, key, value)

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

    db.commit()
    db.refresh(case)
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
