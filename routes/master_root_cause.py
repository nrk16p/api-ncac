from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from models.master_root_cause import MasterRootCause
from schemas.master_root_cause import RootCauseCreate, RootCauseResponse

router = APIRouter(prefix="/master-root-causes", tags=["Master Root Cause"])


# ✅ Create
@router.post("/", response_model=RootCauseResponse, status_code=201)
def create_root_cause(payload: RootCauseCreate, db: Session = Depends(get_db)):

    # 🔥 prevent duplicate
    exists = db.query(MasterRootCause).filter(
        MasterRootCause.root_cause == payload.root_cause
    ).first()

    if exists:
        raise HTTPException(status_code=400, detail="Root cause already exists")

    obj = MasterRootCause(root_cause=payload.root_cause)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


# ✅ Read all
@router.get("/", response_model=List[RootCauseResponse])
def get_root_causes(db: Session = Depends(get_db)):
    return db.query(MasterRootCause).order_by(MasterRootCause.id.desc()).all()


# ✅ Read by id
@router.get("/{id}", response_model=RootCauseResponse)
def get_root_cause(id: int, db: Session = Depends(get_db)):
    obj = db.get(MasterRootCause, id)
    if not obj:
        raise HTTPException(status_code=404, detail="Not found")
    return obj


# ✅ Update
@router.put("/{id}", response_model=RootCauseResponse)
def update_root_cause(id: int, payload: RootCauseCreate, db: Session = Depends(get_db)):
    obj = db.get(MasterRootCause, id)
    if not obj:
        raise HTTPException(status_code=404, detail="Not found")

    obj.root_cause = payload.root_cause

    db.commit()
    db.refresh(obj)
    return obj


# ✅ Delete
@router.delete("/{id}")
def delete_root_cause(id: int, db: Session = Depends(get_db)):
    obj = db.get(MasterRootCause, id)
    if not obj:
        raise HTTPException(status_code=404, detail="Not found")

    db.delete(obj)
    db.commit()
    return {"message": f"Root cause {id} deleted"}