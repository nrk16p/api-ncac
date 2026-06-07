from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import extract
from typing import List
from database import get_db
from models import inspection as models

router = APIRouter(prefix="/performance")

@router.get("/{trainer_id}")
def get_performance(
    trainer_id: str,
    years: List[int] = Query(default=[]),
    months: List[int] = Query(default=[]),
    db: Session = Depends(get_db)
):
    query = db.query(models.InspectionTask).filter(
        models.InspectionTask.trainer_id == trainer_id
    )

    if years:
        query = query.filter(extract("year", models.InspectionTask.plan_date).in_(years))
    if months:
        query = query.filter(extract("month", models.InspectionTask.plan_date).in_(months))

    tasks = query.all()

    if not tasks:
        return {
            "summary": {"open_count": 0, "pending_count": 0, "completed_count": 0, "total_count": 0},
            "open": [], "pending": [], "completed": []
        }

    drivers = db.query(models.InspectionTaskDriver).filter(
        models.InspectionTaskDriver.inspection_task_id.in_(
            [t.inspection_task_id for t in tasks]
        )
    ).all()

    open_drivers = [
        d.inspection_task_driver_id
        for d in drivers if d.inspection_task_driver_status == "open"
    ]
    pending_drivers = [
        d.inspection_task_driver_id
        for d in drivers if d.inspection_task_driver_status == "pending"
    ]
    completed_drivers = [
        d.inspection_task_driver_id
        for d in drivers if d.inspection_task_driver_status == "completed"
    ]

    return {
        "summary": {
            "open_count": len(open_drivers),
            "pending_count": len(pending_drivers),
            "completed_count": len(completed_drivers),
            "total_count": len(drivers)
        },
        "open": open_drivers,
        "pending": pending_drivers,
        "completed": completed_drivers
    }

