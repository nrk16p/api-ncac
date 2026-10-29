from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from database import get_db
from models import Division, Department

router = APIRouter(prefix="/divisions", tags=["Divisions"])

class DivisionBase(BaseModel):
    division_th: Optional[str] = None
    division_eng: Optional[str] = None
    division_code: Optional[str] = None
    path_id: Optional[int] = None

class DivisionResponse(DivisionBase):
    division_id: int
    class Config:
        orm_mode = True

@router.get("/", response_model=List[DivisionResponse])
def get_divisions(db: Session = Depends(get_db)):
    return db.query(Division).order_by(Division.division_id).all()

@router.get("/{division_id}", response_model=DivisionResponse)
def get_division(division_id: int, db: Session = Depends(get_db)):
    d = db.get(Division, division_id)
    if not d:
        raise HTTPException(status_code=404, detail="Division not found")
    return d

@router.post("/", response_model=DivisionResponse, status_code=201)
def create_division(payload: DivisionBase, db: Session = Depends(get_db)):
    d = Division(**payload.dict())
    db.add(d)
    db.commit()
    db.refresh(d)
    return d

@router.put("/{division_id}", response_model=DivisionResponse)
def update_division(division_id: int, payload: DivisionBase, db: Session = Depends(get_db)):
    d = db.get(Division, division_id)
    if not d:
        raise HTTPException(status_code=404, detail="Division not found")
    for field, value in payload.dict(exclude_unset=True).items():
        setattr(d, field, value)
    db.commit()
    db.refresh(d)
    return d

@router.delete("/{division_id}")
def delete_division(division_id: int, db: Session = Depends(get_db)):
    d = db.get(Division, division_id)
    if not d:
        raise HTTPException(status_code=404, detail="Division not found")
    if db.query(Department).filter(Department.division_id == division_id).first():
        raise HTTPException(status_code=400, detail="Division is still used by departments")
    db.delete(d)
    db.commit()
    return {"message": "Division deleted"}
