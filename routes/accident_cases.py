from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

from database import get_db
import models, schemas

router = APIRouter(prefix="/accident-cases", tags=["Accident Cases"])

# -----------------------------
# Config for document numbering
# -----------------------------
SITE_CODES = {
    2: "LB",
    3: "SB",
    4: "SB",
    5: "LB",
    6: "BP",
}


def generate_document_no_ac(db: Session, site_id: int) -> str:
    """Generate a unique accident case document number based on site and month."""
    site_code = SITE_CODES.get(site_id, "XX")

    # All site_ids with the same site_code
    grouped_sites = [sid for sid, code in SITE_CODES.items() if code == site_code]

    now = datetime.now()
    yymm = now.strftime("%y%m")

    # Month start/end
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if now.month == 12:
        next_month = now.replace(year=now.year + 1, month=1, day=1)
    else:
        next_month = now.replace(month=now.month + 1, day=1)

    # Count existing cases
    count = (
        db.query(models.AccidentCase)
        .filter(
            models.AccidentCase.site_id.in_(grouped_sites),
            models.AccidentCase.record_datetime >= start_of_month,
            models.AccidentCase.record_datetime < next_month,
        )
        .count()
    )

    running = f"{count + 1:03d}"
    return f"AC-{site_code}-{yymm}-{running}"


def calculate_priority(
    estimated_goods_damage_value: Optional[float],
    estimated_vehicle_damage_value: Optional[float],
    actual_goods_damage_value: Optional[float],
    actual_vehicle_damage_value: Optional[float]
) -> Optional[str]:
    """Calculate priority based on sum of goods + vehicle damages."""
    estimate = (estimated_goods_damage_value or 0) + (estimated_vehicle_damage_value or 0)
    actual = (actual_goods_damage_value or 0) + (actual_vehicle_damage_value or 0)

    value = actual if actual not in (None, 0) else estimate
    if value in (None, 0):
        return None

    value = float(value)
    if value < 5000:
        return "Minor"
    elif 5000 <= value <= 50000:
        return "Significant"
    elif 50000 < value <= 500000:
        return "Major"
    else:
        return "Crisis"

# -----------------------------
# Routes
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
    )

    case = models.AccidentCase(
        **payload.dict(exclude={"priority", "document_no_ac", "casestatus"}),  # 🚫 exclude client value
        document_no_ac=doc_no,
        priority=priority,
        casestatus="OPEN"  # ✅ always OPEN on creation
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


@router.get("/", response_model=List[schemas.AccidentCaseResponse])
def get_cases(db: Session = Depends(get_db)):
    """Get all accident cases."""
    return db.query(models.AccidentCase).all()


@router.get("/{case_id}", response_model=schemas.AccidentCaseResponse)
def get_case(case_id: int, db: Session = Depends(get_db)):
    """Get a specific accident case by ID."""
    case = db.query(models.AccidentCase).get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.put("/{case_id}", response_model=schemas.AccidentCaseResponse)
def update_case(case_id: int, payload: schemas.AccidentCaseUpdate, db: Session = Depends(get_db)):
    """Update an existing accident case and recalc priority."""
    case = db.query(models.AccidentCase).get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    for key, value in payload.dict(exclude_unset=True, exclude={"priority", "document_no_ac"}).items():
        setattr(case, key, value)

    # Recalc priority
    case.priority = calculate_priority(
        getattr(case, "estimated_goods_damage_value", None),
        getattr(case, "estimated_vehicle_damage_value", None),
        getattr(case, "actual_goods_damage_value", None),
        getattr(case, "actual_vehicle_damage_value", None),
    )

    db.commit()
    db.refresh(case)
    return case


@router.delete("/{case_id}", status_code=204)
def delete_case(case_id: int, db: Session = Depends(get_db)):
    """Delete an accident case."""
    case = db.query(models.AccidentCase).get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    db.delete(case)
    db.commit()
