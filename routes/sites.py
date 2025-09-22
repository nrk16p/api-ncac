from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from database import get_db
from models import Site

router = APIRouter(prefix="/sites", tags=["Sites"])

class SiteBase(BaseModel):
    site_code: Optional[str] = None
    site_name_th: Optional[str] = None
    site_name_en: Optional[str] = None

class SiteCreate(SiteBase):
    site_code: str
    site_name_th: str
    site_name_en: str

class SiteResponse(SiteBase):
    site_id: int
    class Config:
        orm_mode = True

@router.get("/", response_model=List[SiteResponse])
def get_sites(db: Session = Depends(get_db)):
    return db.query(Site).all()

@router.get("/{site_id}", response_model=SiteResponse)
def get_site(site_id: int, db: Session = Depends(get_db)):
    s = db.query(Site).get(site_id)
    if not s:
        raise HTTPException(status_code=404, detail="Site not found")
    return s

@router.post("/", response_model=SiteResponse, status_code=201)
def create_site(payload: SiteCreate, db: Session = Depends(get_db)):
    s = Site(site_code=payload.site_code, site_name_th=payload.site_name_th, site_name_en=payload.site_name_en)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s

@router.put("/{site_id}")
def update_site(site_id: int, payload: SiteBase, db: Session = Depends(get_db)):
    s = db.query(Site).get(site_id)
    if not s:
        raise HTTPException(status_code=404, detail="Site not found")
    if payload.site_code is not None:
        s.site_code = payload.site_code
    if payload.site_name_th is not None:
        s.site_name_th = payload.site_name_th
    if payload.site_name_en is not None:
        s.site_name_en = payload.site_name_en
    db.commit()
    return {"message": "Site updated"}

@router.delete("/{site_id}")
def delete_site(site_id: int, db: Session = Depends(get_db)):
    s = db.query(Site).get(site_id)
    if not s:
        raise HTTPException(status_code=404, detail="Site not found")
    db.delete(s)
    db.commit()
    return {"message": "Site deleted"}
