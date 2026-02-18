from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models.master_model import FormMaster, FormApprovalRule
from schemas.form_schema import FormApprovalRuleCreate , FormApprovalRuleUpdate

router = APIRouter(prefix="/forms", tags=["Forms - Approval Rules"])


# ============================================================
# ➕ Create / 🔁 Update (Upsert) Approval Rule
# ============================================================
@router.post("/rules")
def create_rule(payload: FormApprovalRuleCreate, db: Session = Depends(get_db)):
    form = db.query(FormMaster).filter(
        FormMaster.form_code == payload.form_code
    ).first()
    if not form:
        raise HTTPException(404, "Form not found")

    # 🚫 ป้องกัน creator range ซ้อน
    overlap = db.query(FormApprovalRule).filter(
        FormApprovalRule.form_master_id == form.id,
        FormApprovalRule.level_no == payload.level_no,
        FormApprovalRule.creator_min <= payload.creator_max,
        FormApprovalRule.creator_max >= payload.creator_min
    ).first()

    if overlap:
        raise HTTPException(
            400,
            "Creator range overlaps with existing rule"
        )

    rule = FormApprovalRule(
        form_master_id=form.id,
        level_no=payload.level_no,
        creator_min=payload.creator_min,
        creator_max=payload.creator_max,
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
        "rule_id": rule.id
    }

@router.put("/rules/{rule_id}")
def update_rule(
    rule_id: int,
    payload: FormApprovalRuleUpdate,
    db: Session = Depends(get_db)
):
    rule = db.query(FormApprovalRule).filter(
        FormApprovalRule.id == rule_id
    ).first()

    if not rule:
        raise HTTPException(404, "Rule not found")

    for field, value in payload.dict(exclude_unset=True).items():
        setattr(rule, field, value)

    db.commit()
    db.refresh(rule)

    return {
        "message": "Rule updated",
        "rule_id": rule.id
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
@router.get("/{form_code}/rules")
def get_rules_by_form_code(
    form_code: str,
    db: Session = Depends(get_db),
):
    # ✅ always get latest version
    form = (
        db.query(FormMaster)
        .filter(
            FormMaster.form_code == form_code,
            FormMaster.is_latest == True
        )
        .first()
    )

    if not form:
        raise HTTPException(status_code=404, detail="Form not found")

    rules = (
        db.query(FormApprovalRule)
        .filter(FormApprovalRule.form_master_id == form.id)
        .order_by(
            FormApprovalRule.level_no.asc(),
            FormApprovalRule.creator_min.asc()
        )
        .all()
    )

    return {
        "form_code": form_code,
        "version": form.version,  # 🔥 useful for frontend
        "count": len(rules),
        "rules": [
            {
                "id": r.id,
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
@router.delete("/rules/{rule_id}")
def delete_rule(
    rule_id: int,
    db: Session = Depends(get_db),
):
    rule = (
        db.query(FormApprovalRule)
        .filter(FormApprovalRule.id == rule_id)
        .first()
    )

    if not rule:
        raise HTTPException(status_code=404, detail="Approval rule not found")

    db.delete(rule)
    db.commit()

    return {
        "message": "Approval rule deleted",
        "rule_id": rule_id
    }