from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from typing import List
from database import get_db
import models, schemas
from models import District , Province


router = APIRouter(prefix="/districts", tags=["Districts"])


@router.get("/", response_model=List[schemas.DistrictResponse])
def get_districts(db: Session = Depends(get_db)):
    districts = db.query(models.District).all()
    results = []
    for d in districts:
        province = db.query(models.Province).get(d.province_id)
        results.append({
            "district_id": d.district_id,
            "district_name_th": d.district_name_th,
            "district_name_en": d.district_name_en,
            "district_code": d.district_code,
            "province_id": d.province_id,
            "province": {
                "province_id": province.province_id,
                "province_name_th": province.province_name_th,
                "province_name_en": province.province_name_en
            } if province else None
        })
    return results


@router.get("/{district_id}", response_model=schemas.DistrictResponse)
def get_district(district_id: int, db: Session = Depends(get_db)):
    district = db.query(models.District).get(district_id)
    if not district:
        raise HTTPException(status_code=404, detail="District not found")
    return district


@router.get("/by-province/{province_id}", response_model=List[schemas.DistrictResponse])
def get_districts_by_province(province_id: int, db: Session = Depends(get_db)):
    return db.query(models.District).filter(models.District.province_id == province_id).all()


@router.post("/", response_model=schemas.DistrictResponse, status_code=201)
def create_district(payload: schemas.DistrictCreate, db: Session = Depends(get_db)):
    district = models.District(**payload.dict())
    db.add(district)
    db.commit()
    db.refresh(district)
    return district


@router.put("/{district_id}", response_model=schemas.DistrictResponse)
def update_district(district_id: int, payload: schemas.DistrictUpdate, db: Session = Depends(get_db)):
    district = db.query(models.District).get(district_id)
    if not district:
        raise HTTPException(status_code=404, detail="District not found")

    for key, value in payload.dict(exclude_unset=True).items():
        setattr(district, key, value)

    db.commit()
    db.refresh(district)
    return district


@router.delete("/{district_id}", status_code=204)
def delete_district(district_id: int, db: Session = Depends(get_db)):
    district = db.query(models.District).get(district_id)
    if not district:
        raise HTTPException(status_code=404, detail="District not found")
    db.delete(district)
    db.commit()
    return
