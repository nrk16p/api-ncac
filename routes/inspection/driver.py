from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime

from database import get_db
from models import inspection as models
from models.master_model import MasterDriver
from schemas import inspection as schemas

# ✅ ต้องมีตัวนี้
router = APIRouter(prefix="/driver")




@router.post("/{inspection_task_id}")
def add_driver(
    inspection_task_id: str,
    payload: schemas.DriverCreate,
    db: Session = Depends(get_db)
):

    task = db.query(models.InspectionTask).filter(
        models.InspectionTask.inspection_task_id == inspection_task_id
    ).first()

    if not task:
        raise HTTPException(404, "Task not found")

    inspection_task_driver_id = f"{inspection_task_id}-{payload.driver_id}"

    exists = db.query(models.InspectionTaskDriver).filter(
        models.InspectionTaskDriver.inspection_task_driver_id == inspection_task_driver_id
    ).first()

    if exists:
        raise HTTPException(400, "Driver already exists")

    # 🔎 ดึงข้อมูลจาก MasterDriver ก่อน
    master = db.query(MasterDriver).filter(
        MasterDriver.driver_id == payload.driver_id
    ).first()

    driver = models.InspectionTaskDriver(
        inspection_task_driver_id=inspection_task_driver_id,
        inspection_task_id=inspection_task_id,
        driver_id=payload.driver_id,
        number_plate=payload.number_plate or (master.number_plate if master else None),
        truck_number=payload.truck_number or (master.truck_number if master else None),
        truck_type=payload.truck_type or (master.truck_type if master else None),
        first_name=master.first_name if master else None,
        last_name=master.last_name if master else None,
        inspection_task_driver_status="pending"
    )

    db.add(driver)
    db.commit()
    db.refresh(driver)

    return driver


@router.get("/{inspection_task_driver_id}")
def get_driver_detail(inspection_task_driver_id: str, db: Session = Depends(get_db)):

    driver = db.query(models.InspectionTaskDriver).filter(
        models.InspectionTaskDriver.inspection_task_driver_id == inspection_task_driver_id
    ).first()

    if not driver:
        raise HTTPException(404, "Driver not found")

    return driver

@router.put("/{inspection_task_driver_id}")
def update_driver(
    inspection_task_driver_id: str,
    payload: schemas.DriverUpdate,   # ✅ เปลี่ยนตรงนี้
    db: Session = Depends(get_db)
):
    driver = db.query(models.InspectionTaskDriver).filter(
        models.InspectionTaskDriver.inspection_task_driver_id == inspection_task_driver_id
    ).first()

    if not driver:
        raise HTTPException(404, "Driver not found")

    # 🔄 update เฉพาะ field ที่ส่งมา
    for key, value in payload.dict(exclude_unset=True).items():
        setattr(driver, key, value)



    db.commit()
    db.refresh(driver)

    return driver

@router.delete("/{inspection_task_driver_id}")
def delete_driver(
    inspection_task_driver_id: str,
    db: Session = Depends(get_db)
):

    driver = db.query(models.InspectionTaskDriver).filter(
        models.InspectionTaskDriver.inspection_task_driver_id == inspection_task_driver_id
    ).first()

    if not driver:
        raise HTTPException(404, "Driver not found")

    db.delete(driver)
    db.commit()

    return {"message": "Driver deleted successfully"}

@router.patch("/{inspection_task_driver_id}/status")
def update_status(
    inspection_task_driver_id: str,
    status: str,
    db: Session = Depends(get_db)
):
    driver = db.query(models.InspectionTaskDriver).filter(
        models.InspectionTaskDriver.inspection_task_driver_id == inspection_task_driver_id
    ).first()

    if not driver:
        raise HTTPException(404, "Driver not found")

    driver.inspection_task_driver_status = status
    db.commit()

    return {"message": "Status updated"}