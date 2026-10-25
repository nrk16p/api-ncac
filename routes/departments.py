from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from pydantic import BaseModel
from typing import List, Optional
from database import get_db
from models import Department, Division

router = APIRouter(prefix="/departments", tags=["Departments"])

class DepartmentBase(BaseModel):
    department_name_th: Optional[str] = None
    department_name_en: Optional[str] = None
    division_id: Optional[int] = None

class DepartmentCreate(DepartmentBase):
    department_name_th: str
    department_name_en: str
    division_id: int

class DepartmentResponse(DepartmentBase):
    department_id: int
    division_th: Optional[str] = None
    division_eng: Optional[str] = None
    division_code: Optional[str] = None

def build_department_response(d: Department):
    division = d.division
    return {
        "department_id": d.department_id,
        "department_name_th": d.department_name_th,
        "department_name_en": d.department_name_en,
        "division_id": d.division_id,
        "division_th": division.division_th if division else None,
        "division_eng": division.division_eng if division else None,
        "division_code": division.division_code if division else None,
    }

def ensure_division_exists(division_id: Optional[int], db: Session):
    if division_id is not None and not db.get(Division, division_id):
        raise HTTPException(status_code=400, detail="Division not found")

@router.get("/", response_model=List[DepartmentResponse])
def get_departments(division_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = db.query(Department).options(joinedload(Department.division))
    if division_id is not None:
        query = query.filter(Department.division_id == division_id)
    return [build_department_response(d) for d in query.order_by(Department.department_id).all()]

@router.get("/{department_id}", response_model=DepartmentResponse)
def get_department(department_id: int, db: Session = Depends(get_db)):
    d = db.query(Department).get(department_id)
    if not d:
        raise HTTPException(status_code=404, detail="Department not found")
    return build_department_response(d)

@router.post("/", response_model=DepartmentResponse, status_code=201)
def create_department(payload: DepartmentCreate, db: Session = Depends(get_db)):
    ensure_division_exists(payload.division_id, db)
    d = Department(
        department_name_th=payload.department_name_th,
        department_name_en=payload.department_name_en,
        division_id=payload.division_id,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return build_department_response(d)

@router.put("/{department_id}")
def update_department(department_id: int, payload: DepartmentBase, db: Session = Depends(get_db)):
    d = db.query(Department).get(department_id)
    if not d:
        raise HTTPException(status_code=404, detail="Department not found")
    if payload.department_name_th is not None:
        d.department_name_th = payload.department_name_th
    if payload.department_name_en is not None:
        d.department_name_en = payload.department_name_en
    if payload.division_id is not None:
        ensure_division_exists(payload.division_id, db)
        d.division_id = payload.division_id
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
