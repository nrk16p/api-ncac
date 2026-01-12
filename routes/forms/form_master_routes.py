from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from database import get_db
from models.master_model import FormMaster, FormQuestion, FormQuestionOption
from schemas.form_schema import FormMasterCreate

router = APIRouter(prefix="/forms", tags=["Forms - Master"])


# ============================================================
# Create Form Master (Template)
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
                    option_filter=getattr(opt, "option_filter", None),  # conditional
                    sort_order=getattr(opt, "sort_order", None)
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
                    {
                        "value": o.option_value,
                        "label": o.option_label,
                        "filter": o.option_filter
                    }
                    for o in q.options
                ]
            }
            for q in sorted(form.questions, key=lambda x: x.sort_order)
        ]
    }


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
