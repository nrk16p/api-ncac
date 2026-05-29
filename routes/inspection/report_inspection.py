from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from models import inspection as models

router = APIRouter(prefix="/report", tags=["Inspection Report"])

VEHICLE_SECTIONS = [
    ("front",  "ด้านหน้า"),
    ("left",   "ด้านซ้าย"),
    ("rear",   "ด้านหลัง"),
    ("right",  "ด้านขวา"),
    ("inside", "ภายในรถ"),
]


def _section_result(items: list) -> str:
    if not items:
        return "—"
    statuses = [i.get("status") for i in items]
    if any(s == "ไม่ผ่าน" for s in statuses):
        return "fail"
    if any(s is None for s in statuses):
        return "pending"
    return "pass"


def _section_fail_items(items: list) -> str:
    failed = []
    for i in items:
        if i.get("status") == "ไม่ผ่าน":
            name = i.get("item", "")
            remark = i.get("remark") or "ไม่มีเหตุผล"
            failed.append(f"{name} ({remark})")
    return ", ".join(failed) if failed else "—"


@router.get("/driver-summary")
def get_driver_summary(
    task_ids: List[str] = Query(..., description="Inspection task IDs to summarize"),
    db: Session = Depends(get_db),
):
    tasks = db.query(models.InspectionTask).filter(
        models.InspectionTask.inspection_task_id.in_(task_ids)
    ).all()

    result = []

    for task in tasks:
        drivers = db.query(models.InspectionTaskDriver).filter(
            models.InspectionTaskDriver.inspection_task_id == task.inspection_task_id
        ).all()

        for d in drivers:
            row: dict = {
                "inspection_task_id": task.inspection_task_id,
                "plan_date": str(task.plan_date)[:10] if task.plan_date else None,
                "action_date": str(task.action_date)[:10] if task.action_date else None,
                "plant_name": task.plant_name,
                "client_name": task.client_name,
                "trainer_id": task.trainer_id,
                "driver_status": d.inspection_task_driver_status,
                "driver_id": d.driver_id,
                "first_name": d.first_name,
                "last_name": d.last_name,
                "number_plate": d.number_plate,
                "truck_number": d.truck_number,
                "truck_type": d.truck_type,
            }

            # --- Drug / Alcohol ---
            drug = (
                db.query(models.DrugTest)
                .filter(models.DrugTest.drug_test_id == d.drug_test_id)
                .first()
            ) if d.drug_test_id else None

            row.update({
                "alcohol": drug.alcohol if drug else None,
                "amfetamin": drug.amfetamin if drug else None,
                "kra": drug.kra if drug else None,
                "thc": drug.thc if drug else None,
                "drug_test_status": drug.drug_test_status if drug else None,
            })

            # --- PPE ---
            ppe = (
                db.query(models.PPETest)
                .filter(models.PPETest.ppe_test_id == d.ppe_test_id)
                .first()
            ) if d.ppe_test_id else None

            row.update({
                "helmet_check": ppe.helmet_check if ppe else None,
                "glasses_check": ppe.glasses_check if ppe else None,
                "mask_check": ppe.mask_check if ppe else None,
                "vest_check": ppe.vest_check if ppe else None,
                "glove_check": ppe.glove_check if ppe else None,
                "safety_shoes_check": ppe.safety_shoes_check if ppe else None,
                "ppe_status": ppe.ppe_status if ppe else None,
            })

            # --- Vehicle ---
            vehicle = (
                db.query(models.VehicleInspect)
                .filter(models.VehicleInspect.vehicle_inspect_id == d.vehicle_inspect_id)
                .first()
            ) if d.vehicle_inspect_id else None

            row["vehicle_status"] = vehicle.vechicle_status if vehicle else None

            checklist = vehicle.checklist if vehicle else {}
            for key, label in VEHICLE_SECTIONS:
                items = checklist.get(key, [])
                row[f"vehicle_{key}_result"] = _section_result(items)
                row[f"vehicle_{key}_fail_items"] = _section_fail_items(items)

            result.append(row)

    return result
