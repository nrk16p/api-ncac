from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
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
    # Step 1: site code
    site_code = SITE_CODES.get(site_id, "XX")

    # Step 2: all site_ids with the same site_code
    grouped_sites = [sid for sid, code in SITE_CODES.items() if code == site_code]

    # Step 3: current YYMM
    now = datetime.now()
    yymm = now.strftime("%y%m")

    # Step 4: month start/end
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if now.month == 12:
        next_month = now.replace(year=now.year + 1, month=1, day=1)
    else:
        next_month = now.replace(month=now.month + 1, day=1)

    # Step 5: count existing cases for this site group and month
    count = (
        db.query(models.AccidentCase)
        .filter(
            models.AccidentCase.site_id.in_(grouped_sites),
            models.AccidentCase.record_datetime >= start_of_month,   # ✅ fixed
            models.AccidentCase.record_datetime < next_month,
        )
        .count()
    )

    running = f"{count + 1:03d}"

    # Step 6: final format
    return f"AC-{site_code}-{yymm}-{running}"


# -----------------------------
# Routes
# -----------------------------
@router.post("/", response_model=schemas.AccidentCaseResponse, status_code=201)
def create_case(payload: schemas.AccidentCaseCreate, db: Session = Depends(get_db)):
    """Create a new accident case with auto-generated document number."""
    doc_no = generate_document_no_ac(db, payload.site_id)

    case = models.AccidentCase(
        **payload.dict(),
        document_no_ac=doc_no   # ✅ fixed name
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
    """Update an existing accident case."""
    case = db.query(models.AccidentCase).get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    for key, value in payload.dict(exclude_unset=True).items():
        setattr(case, key, value)

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
    return
