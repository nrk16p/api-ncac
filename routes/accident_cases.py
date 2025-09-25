from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
import models, schemas
from typing import List

router = APIRouter(prefix="/accident_cases", tags=["Accident Cases"])

@router.post("/", response_model=schemas.AccidentCaseResponse, status_code=201)
def create_case(payload: schemas.AccidentCaseCreate, db: Session = Depends(get_db)):
    case = models.AccidentCase(**payload.dict())
    db.add(case)
    db.commit()
    db.refresh(case)
    return case

@router.get("/", response_model=List[schemas.AccidentCaseResponse])
def get_cases(db: Session = Depends(get_db)):
    return db.query(models.AccidentCase).all()

@router.get("/{case_id}", response_model=schemas.AccidentCaseResponse)
def get_case(case_id: int, db: Session = Depends(get_db)):
    case = db.query(models.AccidentCase).get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case

@router.put("/{case_id}", response_model=schemas.AccidentCaseResponse)
def update_case(case_id: int, payload: schemas.AccidentCaseUpdate, db: Session = Depends(get_db)):
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
    case = db.query(models.AccidentCase).get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    db.delete(case)
    db.commit()
    return
