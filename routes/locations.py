from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from database import get_db
from models import Location

router = APIRouter(prefix="/locations", tags=["Locations"])

        
class LocationBase(BaseModel):
    location_name: Optional[str] = None
    site_id: Optional[int] = None   # 👈 เพิ่ม site_id

class LocationCreate(LocationBase):
    location_name: str
    site_id: int                    # 👈 site_id required ตอนสร้าง

class LocationResponse(LocationBase):
    location_id: int
    class Config:
        orm_mode = True


@router.post("/", response_model=LocationResponse, status_code=201)
def create_location(payload: LocationCreate, db: Session = Depends(get_db)):
    loc = Location(location_name=payload.location_name, site_id=payload.site_id)
    db.add(loc)
    db.commit()
    db.refresh(loc)
    return loc


@router.put("/{location_id}")
def update_location(location_id: int, payload: LocationBase, db: Session = Depends(get_db)):
    loc = db.query(Location).get(location_id)
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")

    if payload.location_name is not None:
        loc.location_name = payload.location_name
    if payload.site_id is not None:              # 👈 update site_id ได้ด้วย
        loc.site_id = payload.site_id

    db.commit()
    return {"message": "Location updated"}

@router.get("/", response_model=List[LocationResponse])
def get_locations(db: Session = Depends(get_db)):
    return db.query(Location).all()



@router.delete("/{location_id}")
def delete_location(location_id: int, db: Session = Depends(get_db)):
    loc = db.query(Location).get(location_id)
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")
    db.delete(loc)
    db.commit()
    return {"message": "Location deleted"}
