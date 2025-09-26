from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from database import get_db
import models, schemas

router = APIRouter(prefix="/provinces", tags=["Provinces"])


@router.get("/", response_model=List[schemas.ProvinceResponse])
def get_provinces(db: Session = Depends(get_db)):
    return db.query(models.Province).all()


@router.get("/{province_id}", response_model=schemas.ProvinceResponse)
def get_province(province_id: int, db: Session = Depends(get_db)):
    province = db.query(models.Province).get(province_id)
    if not province:
        raise HTTPException(status_code=404, detail="Province not found")
    return province


@router.post("/", response_model=schemas.ProvinceResponse, status_code=201)
def create_province(payload: schemas.ProvinceCreate, db: Session = Depends(get_db)):
    province = models.Province(**payload.dict())
    db.add(province)
    db.commit()
    db.refresh(province)
    return province


@router.put("/{province_id}", response_model=schemas.ProvinceResponse)
def update_province(province_id: int, payload: schemas.ProvinceUpdate, db: Session = Depends(get_db)):
    province = db.query(models.Province).get(province_id)
    if not province:
        raise HTTPException(status_code=404, detail="Province not found")

    for key, value in payload.dict(exclude_unset=True).items():
        setattr(province, key, value)

    db.commit()
    db.refresh(province)
    return province


@router.delete("/{province_id}", status_code=204)
def delete_province(province_id: int, db: Session = Depends(get_db)):
    province = db.query(models.Province).get(province_id)
    if not province:
        raise HTTPException(status_code=404, detail="Province not found")
    db.delete(province)
    db.commit()
    return
