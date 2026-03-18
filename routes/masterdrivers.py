from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from database import get_db
from models import MasterDriver
from sqlalchemy.dialects.postgresql import insert

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
    client_name: Optional[str] = None
    plant_code: Optional[str] = None
    plant_name: Optional[str] = None
    truck_number: Optional[str] = None
    truck_type: Optional[str] = None

    number_plate: Optional[str] = None
    month_year: Optional[str] = None

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
        driver_role_id=payload.driver_role_id , driver_id=payload.driver_id
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d

@router.get("/", response_model=List[MasterDriverResponse])
def get_drivers(db: Session = Depends(get_db)):
    return db.query(MasterDriver).all()



@router.post("/bulk-upsert")
def upsert_drivers(payload: List[MasterDriverCreate], db: Session = Depends(get_db)):

    stmt = insert(MasterDriver).values([p.dict() for p in payload])

    stmt = stmt.on_conflict_do_update(
        index_elements=['driver_id'],  # unique key
        set_={
            "first_name": stmt.excluded.first_name,
            "last_name": stmt.excluded.last_name,
            "site_id": stmt.excluded.site_id,
            "driver_role_id": stmt.excluded.driver_role_id,
            "client_name": stmt.excluded.client_name,
            "plant_code": stmt.excluded.plant_code,
            "plant_name": stmt.excluded.plant_name,
            "truck_number": stmt.excluded.truck_number,
            "truck_type": stmt.excluded.truck_type,

            "number_plate": stmt.excluded.number_plate,
            "month_year": stmt.excluded.month_year,
        }
    )

    db.execute(stmt)
    db.commit()

    return {"message": "Upsert success"}