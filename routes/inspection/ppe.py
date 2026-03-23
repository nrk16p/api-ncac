from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import inspection as models
from schemas import inspection as schemas
from .helper import get_driver_or_404


router = APIRouter(prefix="/ppe", tags=["PPE"])


# ✅ CREATE PPE
@router.post("/{inspection_task_driver_id}", response_model=schemas.PPETestResponse)
def add_ppe(
    inspection_task_driver_id: str,
    payload: schemas.PPETestCreate,
    db: Session = Depends(get_db)
):
    driver = get_driver_or_404(inspection_task_driver_id, db)

    # 🔥 ป้องกัน create ซ้ำ
    if driver.ppe_test_id:
        raise HTTPException(400, "PPE already exists for this driver")

    ppe = models.PPETest(**payload.dict())

    db.add(ppe)
    db.commit()
    db.refresh(ppe)

    driver.ppe_test_id = ppe.ppe_test_id
    db.commit()

    return ppe


@router.put("/{inspection_task_driver_id}", response_model=schemas.PPETestResponse)
def update_ppe(
    inspection_task_driver_id: str,
    payload: schemas.PPETestUpdate,
    db: Session = Depends(get_db)
):
    driver = get_driver_or_404(inspection_task_driver_id, db)

    if not driver.ppe_test_id:
        raise HTTPException(404, "PPE not found for this driver")

    ppe = db.query(models.PPETest).filter(
        models.PPETest.ppe_test_id == driver.ppe_test_id
    ).first()

    for key, value in payload.dict(exclude_unset=True).items():
        setattr(ppe, key, value)

    db.commit()
    db.refresh(ppe)

    return ppe


# ✅ GET PPE
@router.get("/{inspection_task_driver_id}", response_model=schemas.PPETestResponse)
def get_ppe(
    inspection_task_driver_id: str,
    db: Session = Depends(get_db)
):
    driver = get_driver_or_404(inspection_task_driver_id, db)

    if not driver.ppe_test_id:
        raise HTTPException(404, "PPE not found for this driver")

    ppe = db.query(models.PPETest).filter(
        models.PPETest.ppe_test_id == driver.ppe_test_id
    ).first()

    return ppe