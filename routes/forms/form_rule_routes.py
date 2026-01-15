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

# ============================================================
# 📄 Get Approval Rules (filter by form_code)
# ============================================================
@router.get("/rules")
def get_rules(form_code: str | None = None, db: Session = Depends(get_db)):
    query = db.query(FormApprovalRule).join(FormMaster)

    if form_code:
        query = query.filter(FormMaster.form_code == form_code)

    rules = query.order_by(FormApprovalRule.level_no).all()

    return {
        "count": len(rules),
        "rules": [
            {
                "id": r.id,
                "form_code": r.form_master.form_code,
                "level_no": r.level_no,
                "creator_min": r.creator_min,
                "creator_max": r.creator_max,
                "approve_by_type": r.approve_by_type,
                "approve_by_min": r.approve_by_min,
                "approve_by_max": r.approve_by_max,
                "same_department": r.same_department,
                "is_active": r.is_active
            }
            for r in rules
        ]
    }
# ============================================================
# 🔄 Update Approval Rule by form_code + level_no
# ============================================================
@router.put("/rules/by-form")
def update_rule_by_form(payload: FormApprovalRuleCreate, db: Session = Depends(get_db)):
    # 1️⃣ หา Form
    form = db.query(FormMaster).filter(FormMaster.form_code == payload.form_code).first()
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")

    # 2️⃣ หา Rule ตาม form + level
    rule = db.query(FormApprovalRule).filter(
        FormApprovalRule.form_master_id == form.id,
        FormApprovalRule.level_no == payload.level_no
    ).first()

    if not rule:
        raise HTTPException(status_code=404, detail="Approval rule not found")

    # 3️⃣ UPDATE
    rule.creator_min = payload.creator_min
    rule.creator_max = payload.creator_max
    rule.approve_by_type = payload.approve_by_type
    rule.approve_by_min = payload.approve_by_min
    rule.approve_by_max = payload.approve_by_max
    rule.same_department = payload.same_department
    rule.is_active = payload.is_active

    db.commit()
    db.refresh(rule)

    return {
        "message": "Approval rule updated",
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
# ============================================================
# ❌ Delete Approval Rule by form_code + level_no
# ============================================================
@router.delete("/rules/by-form")
def delete_rule_by_form(form_code: str, level_no: int, db: Session = Depends(get_db)):
    # 1️⃣ หา Form
    form = db.query(FormMaster).filter(FormMaster.form_code == form_code).first()
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")

    # 2️⃣ หา Rule
    rule = db.query(FormApprovalRule).filter(
        FormApprovalRule.form_master_id == form.id,
        FormApprovalRule.level_no == level_no
    ).first()

    if not rule:
        raise HTTPException(status_code=404, detail="Approval rule not found")

    db.delete(rule)
    db.commit()

    return {
        "message": "Approval rule deleted",
        "form_code": form_code,
        "level_no": level_no
    }
