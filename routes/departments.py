from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from database import get_db
from models import Department

router = APIRouter(prefix="/departments", tags=["Departments"])

class DepartmentBase(BaseModel):
    department_name_th: Optional[str] = None
    department_name_en: Optional[str] = None

class DepartmentCreate(DepartmentBase):
    department_name_th: str
    department_name_en: str

class DepartmentResponse(DepartmentBase):
    department_id: int
    class Config:
        orm_mode = True

@router.get("/", response_model=List[DepartmentResponse])
def get_departments(db: Session = Depends(get_db)):
    return db.query(Department).all()

@router.get("/{department_id}", response_model=DepartmentResponse)
def get_department(department_id: int, db: Session = Depends(get_db)):
    d = db.query(Department).get(department_id)
    if not d:
        raise HTTPException(status_code=404, detail="Department not found")
    return d

@router.post("/", response_model=DepartmentResponse, status_code=201)
def create_department(payload: DepartmentCreate, db: Session = Depends(get_db)):
    d = Department(
        department_name_th=payload.department_name_th,
        department_name_en=payload.department_name_en,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d

@router.put("/{department_id}")
def update_department(department_id: int, payload: DepartmentBase, db: Session = Depends(get_db)):
    d = db.query(Department).get(department_id)
    if not d:
        raise HTTPException(status_code=404, detail="Department not found")
    if payload.department_name_th is not None:
        d.department_name_th = payload.department_name_th
    if payload.department_name_en is not None:
        d.department_name_en = payload.department_name_en
    db.commit()
    return {"message": "Department updated"}

@router.delete("/{department_id}")
def delete_department(department_id: int, db: Session = Depends(get_db)):
    d = db.query(Department).get(department_id)
    if not d:
        raise HTTPException(status_code=404, detail="Department not found")
    db.delete(d)
    db.commit()
    return {"message": "Department deleted"}
