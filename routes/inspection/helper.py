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

# Check drug test status, PPE status, Vehicle inspect status เพื่อคำนวณสถานะรวมของ inspection_task_driver_status โดยแค่ check ว่า ถ้าไม่เป็นช่องว่างแล้ว ก็ปรับสถานะเป็น Complete ได้เลย
def calculate_overall_inspection_status(driver, db: Session):
    statuses = []

    if driver.ppe_test_id:
        ppe = db.query(models.PPETest).filter(
            models.PPETest.ppe_test_id == driver.ppe_test_id
        ).first()
        if ppe:
            statuses.append(ppe.ppe_status)
    if driver.drug_test_id:
        drug = db.query(models.DrugTest).filter(
            models.DrugTest.drug_test_id == driver.drug_test_id
        ).first()
        if drug:
            statuses.append(drug.drug_test_status)
    if driver.vehicle_inspect_id:
        vehicle_inspect = db.query(models.VehicleInspect).filter(
            models.VehicleInspect.vehicle_inspect_id == driver.vehicle_inspect_id
        ).first()
        if vehicle_inspect:
            statuses.append(vehicle_inspect.vechicle_status)   
        
    # ถ้าไม่มีการตรวจเลย ให้เป็น Pending
    if not statuses or all(status == "pending" for status in statuses):
          return "pending"
    else: return "completed"
    