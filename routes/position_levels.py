from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
import models, schemas

router = APIRouter(prefix="/position-levels", tags=["Position Levels"])


@router.post("/", response_model=schemas.PositionLevelResponse)
def create_position_level(payload: schemas.PositionLevelCreate, db: Session = Depends(get_db)):
    new_level = models.PositionLevel(**payload.dict())
    db.add(new_level)
    db.commit()
    db.refresh(new_level)
    return new_level


@router.get("/", response_model=list[schemas.PositionLevelResponse])
def get_position_levels(db: Session = Depends(get_db)):
    return db.query(models.PositionLevel).all()
