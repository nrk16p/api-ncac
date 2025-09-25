from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from database import get_db
from models import MasterCause

router = APIRouter(prefix="/mastercauses", tags=["Master Causes"])

class MasterCauseBase(BaseModel):
    cause_name: str
    description: Optional[str] = None
    site_id: Optional[int] = None

class MasterCauseResponse(MasterCauseBase):
    cause_id: int
    site_id: int
    class Config:
        orm_mode = True

# ✅ Create
@router.post("/", response_model=MasterCauseResponse, status_code=201)
def create_cause(payload: MasterCauseBase, db: Session = Depends(get_db)):
    c = MasterCause(
        cause_name=payload.cause_name,
        description=payload.description,
        site_id=payload.site_id
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c

# ✅ Read
@router.get("/", response_model=List[MasterCauseResponse])
def get_causes(db: Session = Depends(get_db)):
    return db.query(MasterCause).all()

# ✅ Update
@router.put("/{cause_id}", response_model=MasterCauseResponse)
def update_cause(cause_id: int, payload: MasterCauseBase, db: Session = Depends(get_db)):
    c = db.query(MasterCause).get(cause_id)
    if not c:
        raise HTTPException(status_code=404, detail="Cause not found")

    c.cause_name = payload.cause_name
    c.description = payload.description
    if payload.site_id is not None:
        c.site_id = payload.site_id

    db.commit()
    db.refresh(c)
    return c

# ✅ Delete
@router.delete("/{cause_id}")
def delete_cause(cause_id: int, db: Session = Depends(get_db)):
    c = db.query(MasterCause).get(cause_id)
    if not c:
        raise HTTPException(status_code=404, detail="Cause not found")
    db.delete(c)
    db.commit()
    return {"message": f"Cause {cause_id} deleted successfully"}
