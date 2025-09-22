from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from database import get_db
from models import MasterCause

router = APIRouter(prefix="/mastercauses", tags=["Master Causes"])

class MasterCauseBase(BaseModel):
    cause_name: str
    description: Optional[str] = None

class MasterCauseResponse(MasterCauseBase):
    cause_id: int
    class Config:
        orm_mode = True

@router.post("/", response_model=MasterCauseResponse, status_code=201)
def create_cause(payload: MasterCauseBase, db: Session = Depends(get_db)):
    c = MasterCause(cause_name=payload.cause_name, description=payload.description)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c

@router.get("/", response_model=List[MasterCauseResponse])
def get_causes(db: Session = Depends(get_db)):
    return db.query(MasterCause).all()
