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

# ✅ ต้องมีครบทั้ง 3 (PPE, Drug Test, Vehicle Inspect) และทุกตัวต้องไม่เป็น pending / ว่าง
# ถึงจะถือว่า "completed" มิฉะนั้นเป็น "pending"
def calculate_overall_inspection_status(driver, db: Session):
    ppe_status = None
    drug_status = None
    vehicle_status = None

    if driver.ppe_test_id:
        ppe = db.query(models.PPETest).filter(
            models.PPETest.ppe_test_id == driver.ppe_test_id
        ).first()
        if ppe:
            ppe_status = ppe.ppe_status

    if driver.drug_test_id:
        drug = db.query(models.DrugTest).filter(
            models.DrugTest.drug_test_id == driver.drug_test_id
        ).first()
        if drug:
            drug_status = drug.drug_test_status

    if driver.vehicle_inspect_id:
        vehicle_inspect = db.query(models.VehicleInspect).filter(
            models.VehicleInspect.vehicle_inspect_id == driver.vehicle_inspect_id
        ).first()
        if vehicle_inspect:
            vehicle_status = vehicle_inspect.vechicle_status

    statuses = [ppe_status, drug_status, vehicle_status]

    # 🟡 ถ้ายังขาดส่วนใดส่วนหนึ่ง (None) หรือยังมี pending → pending
    if any(s is None or s == "pending" for s in statuses):
        return "pending"

    # 🟢 ครบทั้ง 3 และไม่มี pending → completed
    return "completed"