from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models.master_model import FormMaster, FormApprovalRule
from schemas.form_schema import FormApprovalRuleCreate

router = APIRouter(prefix="/forms", tags=["Forms - Approval Rules"])


# ============================================================
# ➕ Create / 🔁 Update (Upsert) Approval Rule
# ============================================================
@router.post("/rules")
def create_or_update_rule(payload: FormApprovalRuleCreate, db: Session = Depends(get_db)):
    # 1️⃣ หา Form
    form = db.query(FormMaster).filter(FormMaster.form_code == payload.form_code).first()
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")

    # 2️⃣ เช็คว่ามี rule ของ (form_master_id, level_no) อยู่แล้วหรือไม่
    existing = db.query(FormApprovalRule).filter(
        FormApprovalRule.form_master_id == form.id,
        FormApprovalRule.level_no == payload.level_no
    ).first()

    if existing:
        # 🔁 UPDATE
        existing.creator_min = payload.creator_min
        existing.creator_max = payload.creator_max
        existing.approve_by_type = payload.approve_by_type
        existing.approve_by_min = payload.approve_by_min
        existing.approve_by_max = payload.approve_by_max
        existing.same_department = payload.same_department
        existing.is_active = payload.is_active

        db.commit()
        db.refresh(existing)

        return {
            "message": "Approval rule updated",
            "rule": {
                "id": existing.id,
                "form_code": payload.form_code,
                "level_no": existing.level_no,
                "creator_min": existing.creator_min,
                "creator_max": existing.creator_max,
                "approve_by_type": existing.approve_by_type,
                "approve_by_min": existing.approve_by_min,
                "approve_by_max": existing.approve_by_max,
                "same_department": existing.same_department,
                "is_active": existing.is_active
            }
        }

    # 3️⃣ ถ้ายังไม่มี → CREATE
    rule = FormApprovalRule(
        form_master_id=form.id,
        creator_min=payload.creator_min,
        creator_max=payload.creator_max,
        level_no=payload.level_no,
        approve_by_type=payload.approve_by_type,
        approve_by_min=payload.approve_by_min,
        approve_by_max=payload.approve_by_max,
        same_department=payload.same_department,
        is_active=payload.is_active
    )

    db.add(rule)
    db.commit()
    db.refresh(rule)

    return {
        "message": "Approval rule created",
        "rule": {
            "id": rule.id,
            "form_code": payload.form_code,
            "level_no": rule.level_no,
            "creator_min": rule.creator_min,
            "creator_max": rule.creator_max,
            "approve_by_type": rule.approve_by_type,
            "approve_by_min": rule.approve_by_min,
            "approve_by_max": rule.approve_by_max,
            "same_department": rule.same_department,
            "is_active": rule.is_active
        }
    }
