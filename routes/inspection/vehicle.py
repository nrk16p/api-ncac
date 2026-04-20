# routers/vehicle_inspect.py

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from fastapi.encoders import jsonable_encoder
from database import get_db
from models import inspection as models
from schemas import inspection as schemas
from .helper import get_driver_or_404


router = APIRouter(prefix="/vehicle-inspect", tags=["Vehicle Inspect"])

# ======================================================
# 🧠 Helper: Calculate Vehicle Inspect Status
# ======================================================
def calculate_vehicle_inspect_status(inspect):
    values = [] # รวบรวมค่าทั้งหมดจาก checklist ที่เป็น "pass" หรือ "fail" เพื่อใช้ในการคำนวณสถานะ
    for section, items in inspect.checklist.items():
        for item in items:
            if item["status"] is not None:  # ถ้ามี remark แสดงว่าตรวจแล้ว
                values.append(item["status"])
    
    # 🟡 pending (ยังกรอกไม่ครบ)
    if any(v is None for v in values):
        return "pending"

    # 🔴 fail (มีตัวไหน fail)
    if any(v == "fail" for v in values):
        return "fail"

    # 🟢 pass (ผ่านทั้งหมด)
    return "pass"


def convert_checklist(checklist: dict):
    return {
        section: [item.dict() for item in items]
        for section, items in checklist.items()
    }
@router.post("/{inspection_task_driver_id}", response_model=schemas.VehicleInspectResponse)
def create_vehicle_inspect(
    inspection_task_driver_id: str,
    payload: schemas.VehicleInspectCreate,
    db: Session = Depends(get_db)
):
    driver = get_driver_or_404(inspection_task_driver_id, db)

    if driver.vehicle_inspect_id:
        raise HTTPException(400, "Vehicle inspect already exists")

    inspect = models.VehicleInspect(
        checklist=convert_checklist(payload.checklist),  # 🔥 FIX ตรงนี้
        around_check_attachment=payload.around_check_attachment,
        cockpit_attachment=payload.cockpit_attachment
    )
    # ✅ คำนวณ status
    inspect.vechicle_status = calculate_vehicle_inspect_status(inspect)

    db.add(inspect)
    db.commit()
    db.refresh(inspect)

    driver.vehicle_inspect_id = inspect.vehicle_inspect_id
    db.commit()

    return inspect

# ✅ GET by driver
@router.get("/{inspection_task_driver_id}", response_model=schemas.VehicleInspectResponse)
def get_vehicle_inspect(
    inspection_task_driver_id: str,
    db: Session = Depends(get_db)
):
    driver = get_driver_or_404(inspection_task_driver_id, db)

    if not driver.vehicle_inspect_id:
        raise HTTPException(404, "Vehicle inspect not found")

    inspect = db.query(models.VehicleInspect).filter(
        models.VehicleInspect.vehicle_inspect_id == driver.vehicle_inspect_id
    ).first()

    return inspect


@router.put("/{inspection_task_driver_id}", response_model=schemas.VehicleInspectResponse)
def update_vehicle_inspect(
    inspection_task_driver_id: str,
    payload: schemas.VehicleInspectUpdate,
    db: Session = Depends(get_db)
):
    driver = get_driver_or_404(inspection_task_driver_id, db)

    if not driver.vehicle_inspect_id:
        raise HTTPException(404, "Vehicle inspect not found")

    inspect = db.query(models.VehicleInspect).filter(
        models.VehicleInspect.vehicle_inspect_id == driver.vehicle_inspect_id
    ).first()

    # 🔥 FIX HERE
    if payload.checklist is not None:
        inspect.checklist = jsonable_encoder(payload.checklist)

    if payload.around_check_attachment is not None:
        inspect.around_check_attachment = payload.around_check_attachment

    if payload.cockpit_attachment is not None:
        inspect.cockpit_attachment = payload.cockpit_attachment

    # ✅ คำนวณ status
    inspect.vechicle_status = calculate_vehicle_inspect_status(inspect)

    db.commit()
    db.refresh(inspect)

    return inspect

# ✅ DELETE (optional)
@router.delete("/{inspection_task_driver_id}")
def delete_vehicle_inspect(
    inspection_task_driver_id: str,
    db: Session = Depends(get_db)
):
    driver = get_driver_or_404(inspection_task_driver_id, db)

    if not driver.vehicle_inspect_id:
        raise HTTPException(404, "Vehicle inspect not found")

    inspect = db.query(models.VehicleInspect).filter(
        models.VehicleInspect.vehicle_inspect_id == driver.vehicle_inspect_id
    ).first()

    db.delete(inspect)
    driver.vehicle_inspect_id = None
    db.commit()

    return {"message": "deleted"}