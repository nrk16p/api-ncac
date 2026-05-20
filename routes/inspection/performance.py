from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import inspection as models

router = APIRouter(prefix="/performance")

@router.get("/{trainer_id}")
def get_performance(
    trainer_id: str,
    db: Session = Depends(get_db)
):
    tasks = db.query(models.InspectionTask).filter(
        models.InspectionTask.trainer_id == trainer_id
    ).all()

    if not tasks:
        raise HTTPException(404, "No tasks found for this trainer")

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

