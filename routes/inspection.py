from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime

from database import get_db
from models import inspection as models
from schemas import inspection as schemas

router = APIRouter(prefix="/inspection", tags=["inspection"])


# ------------------------------------------------
# HELPER
# ------------------------------------------------

def get_driver_or_404(inspection_task_driver_id: str, db: Session):
    driver = db.query(models.InspectionTaskDriver).filter(
        models.InspectionTaskDriver.inspection_task_driver_id == inspection_task_driver_id
    ).first()

    if not driver:
        raise HTTPException(status_code=404, detail="Driver inspection not found")

    return driver


# ------------------------------------------------
# TASK CRUD
# ------------------------------------------------

@router.post("/task")
def create_task(payload: schemas.InspectionTaskCreate, db: Session = Depends(get_db)):

    date_part = payload.plan_date.strftime("%Y%m%d")
    inspection_task_id = f"{date_part}-{payload.plant_code}"

    exists = db.query(models.InspectionTask).filter(
        models.InspectionTask.inspection_task_id == inspection_task_id
    ).first()

    if exists:
        raise HTTPException(
            status_code=400,
            detail="Inspection task already exists for this plant and date"
        )

    task = models.InspectionTask(
        inspection_task_id=inspection_task_id,
        trainer_id=payload.trainer_id,
        client_name=payload.client_name,
        plant_code=payload.plant_code,
        plan_date=payload.plan_date,
        action_date=payload.action_date,
        inspection_task_status=payload.inspection_task_status
    )

    db.add(task)
    db.commit()
    db.refresh(task)

    return task


@router.get("/task")
def get_tasks(db: Session = Depends(get_db)):
    return db.query(models.InspectionTask).all()


@router.get("/task/{inspection_task_id}")
def get_task(inspection_task_id: str, db: Session = Depends(get_db)):

    task = db.query(models.InspectionTask).filter(
        models.InspectionTask.inspection_task_id == inspection_task_id
    ).first()

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    drivers = db.query(models.InspectionTaskDriver).filter(
        models.InspectionTaskDriver.inspection_task_id == inspection_task_id
    ).all()

    return {
        "task": task,
        "drivers": drivers
    }


@router.delete("/task/{inspection_task_id}")
def delete_task(inspection_task_id: str, db: Session = Depends(get_db)):

    task = db.query(models.InspectionTask).filter(
        models.InspectionTask.inspection_task_id == inspection_task_id
    ).first()

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    db.delete(task)
    db.commit()

    return {"message": "Task deleted"}


# ------------------------------------------------
# ADD DRIVER
# ------------------------------------------------

@router.post("/driver/{inspection_task_id}")
def add_driver(
    inspection_task_id: str,
    payload: schemas.DriverCreate,
    db: Session = Depends(get_db)
):

    task = db.query(models.InspectionTask).filter(
        models.InspectionTask.inspection_task_id == inspection_task_id
    ).first()

    if not task:
        raise HTTPException(status_code=404, detail="Inspection task not found")

    inspection_task_driver_id = f"{inspection_task_id}-{payload.truck_number}"

    exists = db.query(models.InspectionTaskDriver).filter(
        models.InspectionTaskDriver.inspection_task_driver_id == inspection_task_driver_id
    ).first()

    if exists:
        raise HTTPException(
            status_code=400,
            detail="This truck already added to this inspection"
        )

    driver = models.InspectionTaskDriver(
        inspection_task_driver_id=inspection_task_driver_id,
        inspection_task_id=inspection_task_id,
        driver_id=payload.driver_id,
        truck_id=payload.truck_id,
        truck_number=payload.truck_number,
        truck_type=payload.truck_type,
        inspection_task_driver_status="pending"
    )

    db.add(driver)
    db.commit()
    db.refresh(driver)

    return driver


# ------------------------------------------------
# DRUG TEST
# ------------------------------------------------

@router.post("/drug-test/{inspection_task_driver_id}")
def add_drug_test(
    inspection_task_driver_id: str,
    payload: schemas.DrugTestCreate,
    db: Session = Depends(get_db)
):

    driver = get_driver_or_404(inspection_task_driver_id, db)

    drug = models.DrugTest(**payload.dict())

    db.add(drug)
    db.commit()
    db.refresh(drug)

    driver.drug_test_id = drug.drug_test_id
    db.commit()

    return {
        "drug_test_id": drug.drug_test_id,
        "alcohol": drug.alcohol,
        "amfetamin_2": drug.amfetamin_2
    }

# ------------------------------------------------
# PPE TEST
# ------------------------------------------------

@router.post("/ppe/{inspection_task_driver_id}")
def add_ppe(
    inspection_task_driver_id: str,
    payload: schemas.PPETestCreate,
    db: Session = Depends(get_db)
):

    driver = get_driver_or_404(inspection_task_driver_id, db)

    ppe = models.PPETest(**payload.dict())

    db.add(ppe)
    db.commit()
    db.refresh(ppe)

    driver.ppe_test_id = ppe.ppe_test_id
    db.commit()

    return {"succues"}


# ------------------------------------------------
# VEHICLE INSPECT
# ------------------------------------------------

@router.post("/vehicle/{inspection_task_driver_id}")
def add_vehicle(
    inspection_task_driver_id: str,
    payload: schemas.VehicleInspectCreate,
    db: Session = Depends(get_db)
):

    driver = get_driver_or_404(inspection_task_driver_id, db)

    vehicle = models.VehicleInspect(**payload.dict())

    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)

    driver.vehicle_inspect_id = vehicle.vehicle_inspect_id
    db.commit()

    return {"update_vehicle_success"}