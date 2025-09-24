from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
import models, schemas

router = APIRouter(prefix="/positions", tags=["Positions"])


@router.post("/", response_model=schemas.Position)
def create_position(payload: schemas.PositionCreate, db: Session = Depends(get_db)):
    new_position = models.Position(**payload.dict())
    db.add(new_position)
    db.commit()
    db.refresh(new_position)
    return new_position

from sqlalchemy.orm import joinedload


@router.get("/", response_model=list[schemas.Position])
def get_positions(db: Session = Depends(get_db)):
    return (
        db.query(models.Position)
        .options(joinedload(models.Position.level))  # 👈 ใช้ level
        .all()
    )