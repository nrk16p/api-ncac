from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from database import get_db
from models import DriverRole

router = APIRouter(prefix="/driver_roles", tags=["Driver Roles"])

class DriverRoleBase(BaseModel):
    role_name: Optional[str] = None

class DriverRoleCreate(DriverRoleBase):
    role_name: str

class DriverRoleResponse(BaseModel):
    driver_role_id: int
    role_name: str
    class Config:
        orm_mode = True

@router.post("/", response_model=DriverRoleResponse, status_code=201)
def create_driver_role(payload: DriverRoleCreate, db: Session = Depends(get_db)):
    r = DriverRole(role_name=payload.role_name)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r

@router.get("/", response_model=List[DriverRoleResponse])
def get_driver_roles(db: Session = Depends(get_db)):
    return db.query(DriverRole).all()

@router.put("/{role_id}")
def update_driver_role(role_id: int, payload: DriverRoleBase, db: Session = Depends(get_db)):
    r = db.query(DriverRole).get(role_id)
    if not r:
        raise HTTPException(status_code=404, detail="Driver role not found")
    if payload.role_name is not None:
        r.role_name = payload.role_name
    db.commit()
    return {"message": "Driver role updated"}

@router.delete("/{role_id}")
def delete_driver_role(role_id: int, db: Session = Depends(get_db)):
    r = db.query(DriverRole).get(role_id)
    if not r:
        raise HTTPException(status_code=404, detail="Driver role not found")
    db.delete(r)
    db.commit()
    return {"message": "Driver role deleted"}
