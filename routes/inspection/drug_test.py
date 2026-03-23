from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from models import inspection as models
from schemas import inspection as schemas
from .helper import get_driver_or_404

# ✅ สำคัญมาก
router = APIRouter(prefix="/drug-test")

@router.post("/{inspection_task_driver_id}")
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

    db.add(drug)
    db.commit()
    db.refresh(drug)

    driver.drug_test_id = drug.drug_test_id
    db.commit()

    return drug

@router.put("/{inspection_task_driver_id}")
def update_drug_test_by_driver(
    inspection_task_driver_id: str,
    payload: schemas.DrugTestCreate,
    db: Session = Depends(get_db)
):
    # 🔹 หา driver
    driver = get_driver_or_404(inspection_task_driver_id, db)

    # 🔹 check ว่ามี drug test ไหม
    if not driver.drug_test_id:
        raise HTTPException(404, "Drug test not found for this driver")

    # 🔹 หา drug test
    drug = db.query(models.DrugTest).filter(
        models.DrugTest.drug_test_id == driver.drug_test_id
    ).first()

    if not drug:
        raise HTTPException(404, "Drug test not found")

    # 🔹 update fields
    for key, value in payload.dict(exclude_unset=True).items():
        setattr(drug, key, value)

    db.commit()
    db.refresh(drug)

    return drug


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

    return drug