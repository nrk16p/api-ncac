from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from datetime import datetime

from database import get_db
from models.master_model import (
    FormMaster, FormQuestion, FormSubmission,
    FormSubmissionValue, FormSequence, FormApprovalRule
)
from models import User, Position
from schemas.form_schema import FormSubmissionCreate

router = APIRouter(prefix="/forms", tags=["Forms - Submission"])


# ----------------------------
# Helpers
# ----------------------------
def generate_form_id(db: Session, form_code: str) -> str:
    year = datetime.utcnow().year
    seq = (
        db.query(FormSequence)
        .filter(FormSequence.form_code == form_code, FormSequence.year == year)
        .with_for_update()
        .first()
    )
    if not seq:
        seq = FormSequence(form_code=form_code, year=year, last_number=0)
        db.add(seq)
        db.flush()

    seq.last_number += 1
    return f"{form_code}-{year}-{str(seq.last_number).zfill(4)}"


def get_employee_position_level(db: Session, employee_id: str) -> int | None:
    user = db.query(User).filter(User.employee_id == employee_id).first()
    if not user or not user.position_id:
        return None
    pos = db.query(Position).filter(Position.position_id == user.position_id).first()
    return pos.position_level_id if pos else None


def determine_initial_approval(db: Session, form_id: int, creator_level: int | None):
    """
    ใช้ Mapping Rules:
    - หา rule ที่ creator_level อยู่ในช่วง creator_min..creator_max
    - ถ้า approve_by_type = 'auto' → Approved
    - ถ้าเป็น approver (single / range) → In Progress ที่ level_no
    """
    rules = (
        db.query(FormApprovalRule)
        .filter(
            FormApprovalRule.form_master_id == form_id,
            FormApprovalRule.is_active == True
        )
        .order_by(FormApprovalRule.level_no.asc())
        .all()
    )

    if not rules or creator_level is None:
        return "Approved", None

    for rule in rules:
        if rule.creator_min <= creator_level <= rule.creator_max:

            if rule.approve_by_type == "auto":
                return "Approved", None

            if rule.approve_by_type in ["position_level", "position_level_range"]:
                return "In Progress", rule.level_no

            return "Approved", None

    return "Approved", None


# ----------------------------
# Submit
# ----------------------------
@router.post("/submit")
def submit_form(payload: FormSubmissionCreate, db: Session = Depends(get_db)):
    form = db.query(FormMaster).filter(FormMaster.form_code == payload.form_code).first()
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")

    if form.form_status != "Active":
        raise HTTPException(status_code=400, detail="Form is not active")

    try:
        form_id = generate_form_id(db, form.form_code)
        creator_level = get_employee_position_level(db, payload.created_by)

        if not form.need_approval:
            status, current_level = "Approved", None
        else:
            status, current_level = determine_initial_approval(db, form.id, creator_level)

        submission = FormSubmission(
            form_master_id=form.id,
            form_id=form_id,
            created_by=payload.created_by,
            updated_by=payload.updated_by or payload.created_by,
            status_approve=status,
            current_approval_level=current_level
        )
        db.add(submission)
        db.flush()

        submitted = {v.question_id: v for v in payload.values}
        for q in form.questions:
            if q.is_required and q.id not in submitted:
                raise HTTPException(400, f"Missing required field: {q.question_label}")

        for v in payload.values:
            q = db.query(FormQuestion).filter(FormQuestion.id == v.question_id).first()
            if not q:
                raise HTTPException(400, f"Invalid question_id {v.question_id}")

            if q.question_type in ["text", "longtext"] and q.is_required and not v.value_text:
                raise HTTPException(400, f"{q.question_label} is required")

            elif q.question_type == "dropdown" and q.is_required and not v.value_text:
                raise HTTPException(400, f"{q.question_label} is required")

            elif q.question_type == "multiselect":
                if q.is_required and not v.value_text:
                    raise HTTPException(400, f"{q.question_label} must select at least one option")
                if isinstance(v.value_text, list):
                    v.value_text = ",".join(v.value_text)

            elif q.question_type == "checkbox" and v.value_boolean is None:
                raise HTTPException(400, f"{q.question_label} must be true or false")

            elif q.question_type in ["number", "int"] and q.is_required and v.value_number is None:
                raise HTTPException(400, f"{q.question_label} must be a number")

            elif q.question_type in ["date", "datetime"] and q.is_required and not v.value_date:
                raise HTTPException(400, f"{q.question_label} must be a date")

            record = FormSubmissionValue(
                submission_id=submission.id,
                question_id=v.question_id,
                value_text=v.value_text,
                value_number=v.value_number,
                value_date=v.value_date,
                value_boolean=v.value_boolean
            )
            db.add(record)

        db.commit()

        return {
            "message": "Form submitted",
            "submission_id": submission.id,
            "form_id": submission.form_id,
            "status_approve": submission.status_approve,
            "current_approval_level": submission.current_approval_level
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
