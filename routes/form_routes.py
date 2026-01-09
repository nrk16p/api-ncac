from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models.master_model import (
    FormMaster, FormQuestion, FormQuestionOption,
    FormSubmission, FormSubmissionValue
)
from schemas.form_schema import FormMasterCreate, FormSubmissionCreate
from datetime import datetime
from sqlalchemy.exc import IntegrityError
from models.master_model import FormSequence

router = APIRouter(prefix="/forms", tags=["Forms"])
def generate_form_id(db: Session, form_code: str) -> str:
    year = datetime.utcnow().year

    try:
        # 🔒 Lock the sequence row for update (prevents race conditions)
        seq = (
            db.query(FormSequence)
            .filter(
                FormSequence.form_code == form_code,
                FormSequence.year == year
            )
            .with_for_update()
            .first()
        )

        # If no sequence yet → create one
        if not seq:
            seq = FormSequence(
                form_code=form_code,
                year=year,
                last_number=0
            )
            db.add(seq)
            db.flush()   # ensure row exists before increment

        # Increment running number
        seq.last_number += 1
        running = str(seq.last_number).zfill(4)

        return f"{form_code}-{year}-{running}"

    except IntegrityError:
        db.rollback()
        raise

@router.post("/master")
def create_form_master(payload: FormMasterCreate, db: Session = Depends(get_db)):
    exist = db.query(FormMaster).filter(FormMaster.form_code == payload.form_code).first()
    if exist:
        raise HTTPException(status_code=400, detail="Form code already exists")

    form = FormMaster(
        form_type=payload.form_type,
        form_code=payload.form_code,
        form_name=payload.form_name,
        form_status=payload.form_status,      # ✅ COMMA HERE
        need_approval=payload.need_approval   # ✅ NEXT FIELD
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

        if q.question_type in ["dropdown", "multiselect"]:
            for opt in q.options:
                option = FormQuestionOption(
                    question_id=question.id,
                    option_value=opt.option_value,
                    option_label=opt.option_label,
                    sort_order=opt.sort_order
                )
                db.add(option)

    db.commit()
    return {"message": "Form template created", "form_code": form.form_code}


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
        "questions": [
            {
                "id": q.id,
                "name": q.question_name,
                "label": q.question_label,
                "type": q.question_type,
                "required": q.is_required,
                "options": [
                    {"value": o.option_value, "label": o.option_label}
                    for o in q.options
                ]
            } for q in sorted(form.questions, key=lambda x: x.sort_order)
        ]
    }

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
        db.flush()   # 🔑 MUST generate submission.id

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

        # 5️⃣ Save values
        for v in payload.values:
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
    
@router.patch("/{form_code}/status")
def update_form_status(form_code: str, status: str, db: Session = Depends(get_db)):
    form = db.query(FormMaster).filter(FormMaster.form_code == form_code).first()
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")

    if status not in ["Draft", "Active", "Archived"]:
        raise HTTPException(status_code=400, detail="Invalid status")

    form.form_status = status
    db.commit()

    return {
        "message": "Form status updated",
        "form_code": form.form_code,
        "form_status": form.form_status
    }

@router.get("/submission/{submission_id}")
def get_submission(submission_id: int, db: Session = Depends(get_db)):
    submission = db.query(FormSubmission).filter(FormSubmission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    return {
        "submission_id": submission.id,
        "form_id": submission.form_id,   # ✅ BUSINESS ID
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