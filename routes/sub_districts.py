from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from database import get_db
import models, schemas

router = APIRouter(prefix="/sub-districts", tags=["SubDistricts"])


@router.get("/", response_model=List[schemas.SubDistrictResponse])
def get_subdistricts(db: Session = Depends(get_db)):
    return db.query(models.SubDistrict).all()


@router.get("/{sub_id}", response_model=schemas.SubDistrictResponse)
def get_subdistrict(sub_id: int, db: Session = Depends(get_db)):
    sub = db.query(models.SubDistrict).get(sub_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Sub-district not found")
    return sub


@router.get("/by-district/{district_id}", response_model=List[schemas.SubDistrictResponse])
def get_subdistricts_by_district(district_id: int, db: Session = Depends(get_db)):
    return db.query(models.SubDistrict).filter(models.SubDistrict.district_id == district_id).all()


@router.post("/", response_model=schemas.SubDistrictResponse, status_code=201)
def create_subdistrict(payload: schemas.SubDistrictCreate, db: Session = Depends(get_db)):
    sub = models.SubDistrict(**payload.dict())
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


@router.put("/{sub_id}", response_model=schemas.SubDistrictResponse)
def update_subdistrict(sub_id: int, payload: schemas.SubDistrictUpdate, db: Session = Depends(get_db)):
    sub = db.query(models.SubDistrict).get(sub_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Sub-district not found")

    for key, value in payload.dict(exclude_unset=True).items():
        setattr(sub, key, value)

    db.commit()
    db.refresh(sub)
    return sub


@router.delete("/{sub_id}", status_code=204)
def delete_subdistrict(sub_id: int, db: Session = Depends(get_db)):
    sub = db.query(models.SubDistrict).get(sub_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Sub-district not found")
    db.delete(sub)
    db.commit()
    return
