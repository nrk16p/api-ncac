from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from fastapi.encoders import jsonable_encoder
from copy import deepcopy

from database import get_db
from models import inspection as models
from schemas import inspection as schemas
from datetime import datetime
from sqlalchemy.orm.attributes import flag_modified
router = APIRouter(prefix="/safety-talk", tags=["Safety Talk"])


@router.post("/{inspection_task_id}", response_model=schemas.SafetyTalkResponse)
def create_safety_talk(
    inspection_task_id: str,
    payload: schemas.SafetyTalkCreate,
    db: Session = Depends(get_db)
):
    safety = models.SafetyTalk(
        inspection_task_id=inspection_task_id,
        topics=jsonable_encoder(payload.topics),
        attendance=jsonable_encoder(payload.attendance),
        noted=payload.noted,
        upload_url=payload.upload_url
    )

    db.add(safety)
    db.commit()
    db.refresh(safety)

    return safety


@router.put("/{inspection_task_id}", response_model=schemas.SafetyTalkResponse)
def update_safety_talk(
    inspection_task_id: str,
    payload: schemas.SafetyTalkUpdate,
    db: Session = Depends(get_db)
):
    safety = db.query(models.SafetyTalk).filter(
        models.SafetyTalk.inspection_task_id == inspection_task_id
    ).first()

    if not safety:
        raise HTTPException(404, "Safety talk not found")

    for key, value in payload.dict(exclude_unset=True).items():
        if key in ["topics", "attendance"]:
            value = jsonable_encoder(value)
        setattr(safety, key, value)

    db.commit()
    db.refresh(safety)

    return safety

@router.put("/{inspection_task_id}/attendance/{driver_id}")
def update_attendance_status(
    inspection_task_id: str,
    driver_id: str,
    status: str,
    db: Session = Depends(get_db)
):
    safety = db.query(models.SafetyTalk).filter(
        models.SafetyTalk.inspection_task_id == inspection_task_id
    ).first()

    if not safety:
        raise HTTPException(404, "Safety talk not found")

    if not safety.attendance:
        raise HTTPException(400, "Attendance is empty - cannot update")

    attendance = deepcopy(safety.attendance)

    for a in attendance:
        if str(a["driver_id"]) == str(driver_id):
            a["status"] = status
            a["date"] = datetime.now().date().isoformat()
            break

    safety.attendance = attendance
    flag_modified(safety, "attendance")

    db.commit()
    db.refresh(safety)

    return safety

@router.get("/{inspection_task_id}", response_model=schemas.SafetyTalkResponse)
def get_safety_talk(
    inspection_task_id: str,
    db: Session = Depends(get_db)
):
    safety = db.query(models.SafetyTalk).filter(
        models.SafetyTalk.inspection_task_id == inspection_task_id
    ).first()

    if not safety:
        raise HTTPException(404, "Safety talk not found")

    return safety