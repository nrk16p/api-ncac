from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import inspection as models
from schemas import inspection as schemas
from .helper import get_driver_or_404, calculate_overall_inspection_status

router = APIRouter(prefix="/ppe", tags=["PPE"])


# ======================================================
# 🧠 Helper: Calculate PPE Status
# ======================================================
def calculate_ppe_status(ppe):
    values = [
        ppe.helmet_check,
        ppe.glasses_check,
        ppe.mask_check,
        ppe.vest_check,
        ppe.glove_check,
        ppe.safety_shoes_check,
    ]

    # 🟡 pending (ยังกรอกไม่ครบ)
    if any(v is None for v in values):
        return "pending"

    # 🔴 fail (มีตัวไหน fail)
    if any(v == "ไม่มี" or v == "ชำรุด" for v in values):
        return "fail"

    # 🟢 pass (ผ่านทั้งหมด)
    return "pass"


# ======================================================
# 🚀 CREATE PPE
# ======================================================
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

    # ✅ คำนวณ status
    ppe.ppe_status = calculate_ppe_status(ppe)

    db.add(ppe)
    db.commit()
    db.refresh(ppe)

    # 🔗 bind กับ driver
    driver.ppe_test_id = ppe.ppe_test_id
    driver.inspection_task_driver_status = calculate_overall_inspection_status(driver, db)
    db.commit()

    return ppe


# ======================================================
# 🔄 UPDATE PPE
# ======================================================
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

    if not ppe:
        raise HTTPException(404, "PPE record not found")

    # 🔄 update field
    for key, value in payload.dict(exclude_unset=True).items():
        setattr(ppe, key, value)

    # ✅ recalculate status ทุกครั้ง
    ppe.ppe_status = calculate_ppe_status(ppe)

    driver.inspection_task_driver_status = calculate_overall_inspection_status(driver, db)
    db.commit()
    db.refresh(ppe)

    return ppe


# ======================================================
# 📥 GET PPE
# ======================================================
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

    if not ppe:
        raise HTTPException(404, "PPE record not found")

    return ppe


# ======================================================
# 📊 OPTIONAL: GET ALL PPE (filter ได้)
# ======================================================
@router.get("/", response_model=list[schemas.PPETestResponse])
def list_ppe(
    status: str | None = None,
    db: Session = Depends(get_db)
):
    query = db.query(models.PPETest)

    if status:
        query = query.filter(models.PPETest.ppe_status == status)

    return query.all()