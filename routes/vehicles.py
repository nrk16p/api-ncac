from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from database import get_db
from models import Vehicle

router = APIRouter(prefix="/vehicles", tags=["Vehicles"])

class VehicleBase(BaseModel):
    vehicle_number_plate: Optional[str] = None
    truck_no: Optional[str] = None

class VehicleCreate(VehicleBase):
    vehicle_number_plate: str

class VehicleResponse(BaseModel):
    vehicle_id: int
    vehicle_number_plate: str
    truck_no: Optional[str] = None
    class Config:
        orm_mode = True

@router.post("/", response_model=VehicleResponse, status_code=201)
def create_vehicle(payload: VehicleCreate, db: Session = Depends(get_db)):
    v = Vehicle(vehicle_number_plate=payload.vehicle_number_plate, truck_no=payload.truck_no or "")
    db.add(v)
    db.commit()
    db.refresh(v)
    return v

@router.get("/", response_model=List[VehicleResponse])
def get_vehicles(db: Session = Depends(get_db)):
    return db.query(Vehicle).all()

@router.put("/{vehicle_id}")
def update_vehicle(vehicle_id: int, payload: VehicleBase, db: Session = Depends(get_db)):
    v = db.query(Vehicle).get(vehicle_id)
    if not v:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    if payload.vehicle_number_plate is not None:
        v.vehicle_number_plate = payload.vehicle_number_plate
    if payload.truck_no is not None:
        v.truck_no = payload.truck_no
    db.commit()
    return {"message": "Vehicle updated"}

@router.delete("/{vehicle_id}")
def delete_vehicle(vehicle_id: int, db: Session = Depends(get_db)):
    v = db.query(Vehicle).get(vehicle_id)
    if not v:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    db.delete(v)
    db.commit()
    return {"message": "Vehicle deleted"}
