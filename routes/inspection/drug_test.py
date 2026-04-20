from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import inspection as models
from schemas import inspection as schemas
from .helper import get_driver_or_404, calculate_overall_inspection_status

router = APIRouter(prefix="/drug-test")


# ======================================================
# 🧠 Helper: Calculate Drug Test Status
# ======================================================
def calculate_drug_status(drug):
    values = [
        drug.alcohol,
        drug.amfetamin,
        drug.kra,
        drug.thc
    ]

    # 🟡 ถ้ายังมี None → pending
    if any(v is None for v in values):
        return "pending"

    # 🔴 fail case
    if drug.alcohol > 0:
        return "fail"

    if (
        drug.amfetamin == "พบสาร" or
        drug.kra == "พบสาร" or
        drug.thc == "พบสาร"
    ):
        return "fail"

    # 🟢 pass
    return "pass"


# ======================================================
# 🚀 CREATE
# ======================================================
@router.post("/{inspection_task_driver_id}", response_model=schemas.DrugTestResponse)
def add_drug_test(
    inspection_task_driver_id: str,
    payload: schemas.DrugTestCreate,
    db: Session = Depends(get_db)
):
    driver = get_driver_or_404(inspection_task_driver_id, db)

    # ❌ ป้องกันซ้ำ
    if driver.drug_test_id:
        raise HTTPException(400, "Drug test already exists for this driver")

    drug = models.DrugTest(**payload.dict())

    # ✅ auto status
    drug.drug_test_status = calculate_drug_status(drug)

    db.add(drug)
    db.commit()
    db.refresh(drug)

    # 🔗 link กลับไปที่ driver
    driver.drug_test_id = drug.drug_test_id
    driver.inspection_task_driver_status = calculate_overall_inspection_status(driver, db)
    db.commit()

    return drug


# ======================================================
# 🔄 UPDATE
# ======================================================
@router.put("/{inspection_task_driver_id}", response_model=schemas.DrugTestResponse)
def update_drug_test_by_driver(
    inspection_task_driver_id: str,
    payload: schemas.DrugTestCreate,
    db: Session = Depends(get_db)
):
    driver = get_driver_or_404(inspection_task_driver_id, db)

    if not driver.drug_test_id:
        raise HTTPException(404, "Drug test not found for this driver")

    drug = db.query(models.DrugTest).filter(
        models.DrugTest.drug_test_id == driver.drug_test_id
    ).first()

    if not drug:
        raise HTTPException(404, "Drug test not found")

    # 🔄 update fields
    for key, value in payload.dict(exclude_unset=True).items():
        setattr(drug, key, value)

    # ✅ recalc status
    drug.drug_test_status = calculate_drug_status(drug)

    driver.inspection_task_driver_status = calculate_overall_inspection_status(driver, db)
    db.commit()
    db.refresh(drug)

    return drug


# ======================================================
# 🔍 GET
# ======================================================
@router.get("/{inspection_task_driver_id}", response_model=schemas.DrugTestResponse)
def get_drug_test_by_driver(
    inspection_task_driver_id: str,
    db: Session = Depends(get_db)
):
    driver = get_driver_or_404(inspection_task_driver_id, db)

    if not driver.drug_test_id:
        raise HTTPException(404, "Drug test not found for this driver")

    drug = db.query(models.DrugTest).filter(
        models.DrugTest.drug_test_id == driver.drug_test_id
    ).first()

    if not drug:
        raise HTTPException(404, "Drug test not found")

    return drug