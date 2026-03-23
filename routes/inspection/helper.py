from fastapi import HTTPException
from sqlalchemy.orm import Session
from models import inspection as models


def get_driver_or_404(inspection_task_driver_id: str, db: Session):
    driver = db.query(models.InspectionTaskDriver).filter(
        models.InspectionTaskDriver.inspection_task_driver_id == inspection_task_driver_id
    ).first()

    if not driver:
        raise HTTPException(status_code=404, detail="Driver inspection not found")

    return driver