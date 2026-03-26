from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from fastapi.encoders import jsonable_encoder

from database import get_db
from models import inspection as models
from schemas import inspection as schemas
from models.master_model import MasterDriver
from models import inspection as models


router = APIRouter(prefix="/task")


@router.post("/")
def create_task(
    payload: schemas.InspectionTaskCreate,
    db: Session = Depends(get_db)
):
    # -----------------------------
    # GENERATE TASK ID
    # -----------------------------
    date_part = payload.plan_date.strftime("%Y%m%d")
    inspection_task_id = f"{date_part}-{payload.plant_code}"

    # -----------------------------
    # CHECK EXIST TASK
    # -----------------------------
    exists = db.query(models.InspectionTask).filter(
        models.InspectionTask.inspection_task_id == inspection_task_id
    ).first()

    if exists:
        raise HTTPException(400, "Inspection task already exists")

    # -----------------------------
    # CREATE TASK
    # -----------------------------
    task = models.InspectionTask(
        inspection_task_id=inspection_task_id,
        trainer_id=payload.trainer_id,
        client_name=payload.client_name,
        plant_code=payload.plant_code,
        plant_name=payload.plant_name,
        plan_date=payload.plan_date,
        action_date=payload.action_date,
        inspection_task_status=payload.inspection_task_status
    )

    try:
        db.add(task)

        # -----------------------------
        # GET DRIVERS
        # -----------------------------
        drivers = db.query(MasterDriver).filter(
            MasterDriver.client_name == payload.client_name,
            MasterDriver.plant_code == payload.plant_code,
            MasterDriver.plant_name == payload.plant_name
        ).all()

        if not drivers:
            raise HTTPException(404, "No drivers found")

        # -----------------------------
        # UNIQUE DRIVERS (กัน duplicate)
        # -----------------------------
        drivers_unique = {d.driver_id: d for d in drivers}.values()

        # -----------------------------
        # GET EXISTING DRIVER IDS
        # -----------------------------
        existing_ids = set(
            r[0] for r in db.query(
                models.InspectionTaskDriver.inspection_task_driver_id
            ).filter(
                models.InspectionTaskDriver.inspection_task_id == inspection_task_id
            ).all()
        )

        # -----------------------------
        # CREATE DRIVER RECORDS
        # -----------------------------
        created = 0

        for d in drivers_unique:

            inspection_task_driver_id = f"{inspection_task_id}-{d.driver_id}"

            if inspection_task_driver_id in existing_ids:
                continue

            driver = models.InspectionTaskDriver(
                inspection_task_driver_id=inspection_task_driver_id,
                inspection_task_id=inspection_task_id,
                driver_id=d.driver_id,
                number_plate=d.number_plate,
                truck_number=d.truck_number,
                truck_type=d.truck_type,
                first_name=d.first_name,
                last_name=d.last_name,
                inspection_task_driver_status="pending"
            )

            db.add(driver)
            created += 1

        # -----------------------------
        # CREATE SAFETY TALK (AUTO)
        # -----------------------------
        existing_safety = db.query(models.SafetyTalk).filter(
            models.SafetyTalk.inspection_task_id == inspection_task_id
        ).first()

        if not existing_safety:

            attendance = [
                {
                    "driver_id": d.driver_id,
                    "first_name": d.first_name,
                    "last_name": d.last_name,
                    "status": "pending",
                    "date": None
                }
                for d in drivers_unique
            ]

            safety = models.SafetyTalk(
                inspection_task_id=inspection_task_id,
                topics=[],
                attendance=jsonable_encoder(attendance),
                noted=None,
                upload_url=None
            )

            db.add(safety)

        # -----------------------------
        # COMMIT
        # -----------------------------
        db.commit()

        return {
            "inspection_task_id": inspection_task_id,
            "drivers_created": created
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(500, str(e))

@router.get("/")
def get_tasks(db: Session = Depends(get_db)):
    return db.query(models.InspectionTask).all()


@router.get("/{inspection_task_id}")
def get_task(inspection_task_id: str, db: Session = Depends(get_db)):

    task = db.query(models.InspectionTask).filter(
        models.InspectionTask.inspection_task_id == inspection_task_id
    ).first()

    if not task:
        raise HTTPException(404, "Task not found")

    drivers = db.query(models.InspectionTaskDriver).filter(
        models.InspectionTaskDriver.inspection_task_id == inspection_task_id
    ).all()

    result = []

    for d in drivers:
        driver_dict = d.__dict__.copy()

        # ❌ เอา _sa_instance_state ออก
        driver_dict.pop("_sa_instance_state", None)

        # -----------------------------
        # 🔥 ADD PPE STATUS
        # -----------------------------
        if d.ppe_test_id:
            ppe = db.query(models.PPETest).filter(
                models.PPETest.ppe_test_id == d.ppe_test_id
            ).first()

            driver_dict["ppe_status"] = ppe.ppe_status if ppe else None
        else:
            driver_dict["ppe_status"] = None

        # -----------------------------
        # 🔥 ADD DRUG STATUS
        # -----------------------------
        if d.drug_test_id:
            drug = db.query(models.DrugTest).filter(
                models.DrugTest.drug_test_id == d.drug_test_id
            ).first()

            driver_dict["drug_test_status"] = drug.drug_test_status if drug else None
        else:
            driver_dict["drug_test_status"] = None

        result.append(driver_dict)

    return {
        "task": task,
        "drivers": result
    }


@router.delete("/{inspection_task_id}")
def delete_task(inspection_task_id: str, db: Session = Depends(get_db)):

    task = db.query(models.InspectionTask).filter(
        models.InspectionTask.inspection_task_id == inspection_task_id
    ).first()

    if not task:
        raise HTTPException(404, "Task not found")

    db.delete(task)
    db.commit()

    return {"message": "Task deleted"}

@router.put("/{inspection_task_id}")
def update_task(
    inspection_task_id: str,
    payload: schemas.InspectionTaskUpdate,
    db: Session = Depends(get_db)
):
    task = db.query(models.InspectionTask).filter(
        models.InspectionTask.inspection_task_id == inspection_task_id
    ).first()

    if not task:
        raise HTTPException(404, "Task not found")

    try:
        update_data = payload.dict(exclude_unset=True)

        # -----------------------------
        # PROTECT IMMUTABLE FIELDS
        # -----------------------------
        if "plant_code" in update_data:
            raise HTTPException(400, "plant_code cannot be updated")

        if "plan_date" in update_data:
            raise HTTPException(400, "plan_date cannot be updated")

        # -----------------------------
        # UPDATE ONLY SENT FIELDS
        # -----------------------------
        for field, value in update_data.items():
            setattr(task, field, value)

        db.commit()

        return {
            "inspection_task_id": inspection_task_id,
            "message": "Task updated successfully"
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(500, str(e))