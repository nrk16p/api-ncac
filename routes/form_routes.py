from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from datetime import datetime

from database import get_db
from models.master_model import (
    FormMaster, FormQuestion, FormQuestionOption,
    FormSubmission, FormSubmissionValue, FormSequence
)
from schemas.form_schema import FormMasterCreate, FormSubmissionCreate

router = APIRouter(prefix="/forms", tags=["Forms"])


# ============================================================
# Helper: Generate Business Form ID
# Format: <FORM_CODE>-<YEAR>-<0001>
# ============================================================
def generate_form_id(db: Session, form_code: str) -> str:
    year = datetime.utcnow().year

    try:
        # Lock row to avoid race condition
        seq = (
            db.query(FormSequence)
            .filter(
                FormSequence.form_code == form_code,
                FormSequence.year == year
            )
            .with_for_update()
            .first()
        )

        if not seq:
            seq = FormSequence(
                form_code=form_code,
                year=year,
                last_number=0
            )
            db.add(seq)
            db.flush()

        seq.last_number += 1
        running = str(seq.last_number).zfill(4)

        return f"{form_code}-{year}-{running}"

    except IntegrityError as e:
        db.rollback()
        raise e


# ============================================================
# Create Form Master
# ============================================================
@router.post("/master")
def create_form_master(payload: FormMasterCreate, db: Session = Depends(get_db)):
    exist = db.query(FormMaster).filter(FormMaster.form_code == payload.form_code).first()
    if exist:
        raise HTTPException(status_code=400, detail="Form code already exists")

    form = FormMaster(
        form_type=payload.form_type,
        form_code=payload.form_code,
        form_name=payload.form_name,
        form_status=payload.form_status,
        need_approval=payload.need_approval
    )
    db.add(form)
    db.flush()

    for q in payload.questions:
        question = FormQuestion(
            form_master_id=form.id,
            question_name=q.question_name,
            question_label=q.question_label,
            question_type=q.question_type,
            is_required=q.is_required,
            sort_order=q.sort_order
        )
        db.add(question)
        db.flush()

        # Only dropdown & multiselect need options
        if q.question_type in ["dropdown", "multiselect"]:
            for opt in q.options:
                option = FormQuestionOption(
                    question_id=question.id,
                    option_value=opt.option_value,
                    option_label=opt.option_label,
                    option_filter=opt.option_filter,   # 👈 SAVE FILTER
                    sort_order=opt.sort_order
                )
                db.add(option)

    db.commit()
    return {"message": "Form template created", "form_code": form.form_code}


# ============================================================
# Get Form Template
# ============================================================
@router.get("/{form_code}")
def get_form(form_code: str, db: Session = Depends(get_db)):
    form = db.query(FormMaster).filter(FormMaster.form_code == form_code).first()
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")

    return {
        "form_type": form.form_type,
        "form_code": form.form_code,
        "form_name": form.form_name,
        "form_status": form.form_status,
        "need_approval": form.need_approval,
        "questions": [
            {
                "id": q.id,
                "name": q.question_name,
                "label": q.question_label,
                "type": q.question_type,
                "required": q.is_required,
                "options": [
                    {"value": o.option_value, "label": o.option_label ,  "filter": o.option_filter }
                    for o in q.options
                ]
            }
            for q in sorted(form.questions, key=lambda x: x.sort_order)
        ]
    }


# ============================================================
# Submit Form
# ============================================================
@router.post("/submit")
def submit_form(payload: FormSubmissionCreate, db: Session = Depends(get_db)):
    form = db.query(FormMaster).filter(FormMaster.form_code == payload.form_code).first()
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")

    if form.form_status != "Active":
        raise HTTPException(status_code=400, detail="Form is not active")

    try:
        # 1️⃣ Generate business form_id
        form_id = generate_form_id(db, form.form_code)

        # 2️⃣ Determine approval status
        status = "In Progress" if form.need_approval else "Approved"

        # 3️⃣ Create submission
        submission = FormSubmission(
            form_master_id=form.id,
            form_id=form_id,
            created_by=payload.created_by,
            updated_by=payload.updated_by or payload.created_by,
            status_approve=status
        )

        db.add(submission)
        db.flush()   # Must generate submission.id

        if not submission.id:
            raise Exception("submission.id not generated")

        # 4️⃣ Required field validation
        submitted = {v.question_id: v for v in payload.values}
        for q in form.questions:
            if q.is_required and q.id not in submitted:
                raise HTTPException(
                    status_code=400,
                    detail=f"Missing required field: {q.question_label}"
                )

        # 5️⃣ Validate and save values
        for v in payload.values:
            q = db.query(FormQuestion).filter(FormQuestion.id == v.question_id).first()
            if not q:
                raise HTTPException(status_code=400, detail=f"Invalid question_id {v.question_id}")

            # TEXT / LONGTEXT
            if q.question_type in ["text", "longtext"]:
                if q.is_required and not v.value_text:
                    raise HTTPException(status_code=400, detail=f"{q.question_label} is required")

            # DROPDOWN
            elif q.question_type == "dropdown":
                if q.is_required and not v.value_text:
                    raise HTTPException(status_code=400, detail=f"{q.question_label} is required")

            # MULTISELECT
            elif q.question_type == "multiselect":
                if q.is_required and not v.value_text:
                    raise HTTPException(status_code=400, detail=f"{q.question_label} must select at least one option")
                if isinstance(v.value_text, list):
                    v.value_text = ",".join(v.value_text)

            # CHECKBOX (single true/false only)
            elif q.question_type == "checkbox":
                if v.value_boolean is None:
                    raise HTTPException(status_code=400, detail=f"{q.question_label} must be true or false")

            # NUMBER / INT
            elif q.question_type in ["number", "int"]:
                if q.is_required and v.value_number is None:
                    raise HTTPException(status_code=400, detail=f"{q.question_label} must be a number")

            # DATE / DATETIME
            elif q.question_type in ["date", "datetime"]:
                if q.is_required and not v.value_date:
                    raise HTTPException(status_code=400, detail=f"{q.question_label} must be a date")

            else:
                raise HTTPException(status_code=400, detail=f"Unsupported question type: {q.question_type}")

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
            "status_approve": submission.status_approve
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Update Form Status
# ============================================================
@router.patch("/{form_code}/status")
def update_form_status(
    form_code: str,
    status: str = Query(..., regex="^(Draft|Active|Archived|Inactive)$"),
    db: Session = Depends(get_db)
):
    form = db.query(FormMaster).filter(FormMaster.form_code == form_code).first()
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")

    form.form_status = status
    db.commit()

    return {
        "message": "Form status updated",
        "form_code": form.form_code,
        "form_status": form.form_status
    }


# ============================================================
# Get Submission by ID
# ============================================================
@router.get("/submission/{submission_id}")
def get_submission(submission_id: int, db: Session = Depends(get_db)):
    submission = db.query(FormSubmission).filter(FormSubmission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    return {
        "submission_id": submission.id,
        "form_id": submission.form_id,
        "form_code": submission.form.form_code,
        "status_approve": submission.status_approve,
        "created_at": submission.created_at,
        "updated_at": submission.updated_at,
        "created_by": submission.created_by,
        "updated_by": submission.updated_by,
        "values": [
            {
                "question_id": v.question_id,
                "question_name": v.question.question_name,
                "question_label": v.question.question_label,
                "value_text": v.value_text,
                "value_number": v.value_number,
                "value_date": v.value_date,
                "value_boolean": v.value_boolean
            }
            for v in submission.values
        ]
    }
