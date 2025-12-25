from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from database import get_db
from models import MasterDriver

router = APIRouter(prefix="/masterdrivers", tags=["Master Drivers"])

class MasterDriverBase(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    site_id: Optional[int] = None
    driver_role_id: Optional[int] = None

class MasterDriverCreate(MasterDriverBase):
    first_name: str
    last_name: str
    site_id: int
    driver_role_id: int
    driver_id: str


class MasterDriverResponse(MasterDriverBase):
    driver_id: str
    class Config:
        orm_mode = True

@router.post("/", response_model=MasterDriverResponse, status_code=201)
def create_driver(payload: MasterDriverCreate, db: Session = Depends(get_db)):
    d = MasterDriver(
        first_name=payload.first_name,
        last_name=payload.last_name,
        site_id=payload.site_id,
        driver_role_id=payload.driver_role_id
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d

@router.get("/", response_model=List[MasterDriverResponse])
def get_drivers(db: Session = Depends(get_db)):
    return db.query(MasterDriver).all()
