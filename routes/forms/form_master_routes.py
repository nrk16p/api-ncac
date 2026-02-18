from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from database import get_db
from models.master_model import FormMaster, FormQuestion, FormQuestionOption,FormApprovalRule   # 🔥 เพิ่มบรรทัดนี้
from schemas.form_schema import FormMasterCreate , FormMasterUpdate
    


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
    form = db.query(FormMaster).filter(
        FormMaster.form_code == form_code,
        FormMaster.is_latest == True
    ).first()

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
# ============================================================
# VERSIONED UPDATE (CREATE NEW VERSION)
# ============================================================
# ============================================================
# VERSIONED UPDATE (CREATE NEW VERSION SAFELY)
# ============================================================
@router.patch("/master/{form_code}")
def versioned_update_form_master(
    form_code: str,
    payload: FormMasterUpdate,
    db: Session = Depends(get_db)
):
    try:

        # =====================================================
        # 1️⃣ Get current latest version
        # =====================================================
        old_form = (
            db.query(FormMaster)
            .filter(
                FormMaster.form_code == form_code,
                FormMaster.is_latest == True
            )
            .first()
        )

        if not old_form:
            raise HTTPException(status_code=404, detail="Form not found")

        # =====================================================
        # 2️⃣ Mark old version as NOT latest
        # =====================================================
        old_form.is_latest = False
        db.flush()

        # =====================================================
        # 3️⃣ Create new version
        # =====================================================
        new_form = FormMaster(
            form_type=payload.form_type or old_form.form_type,
            form_code=old_form.form_code,
            form_name=payload.form_name or old_form.form_name,
            form_status="Draft",
            need_approval=(
                payload.need_approval
                if payload.need_approval is not None
                else old_form.need_approval
            ),
            version=old_form.version + 1,
            parent_form_id=old_form.id,
            is_latest=True
        )

        db.add(new_form)
        db.flush()

        # =====================================================
        # 4️⃣ Clone Questions + Options
        # =====================================================
        for old_q in old_form.questions:

            cloned_question = FormQuestion(
                form_master_id=new_form.id,
                question_name=old_q.question_name,
                question_label=old_q.question_label,
                question_type=old_q.question_type,
                is_required=old_q.is_required,
                sort_order=old_q.sort_order
            )

            db.add(cloned_question)
            db.flush()

            for old_opt in old_q.options:
                db.add(FormQuestionOption(
                    question_id=cloned_question.id,
                    option_value=old_opt.option_value,
                    option_label=old_opt.option_label,
                    option_filter=old_opt.option_filter,
                    sort_order=old_opt.sort_order
                ))

        db.flush()

        # =====================================================
        # 5️⃣ Clone Approval Rules 🔥 IMPORTANT
        # =====================================================
        old_rules = db.query(FormApprovalRule).filter(
            FormApprovalRule.form_master_id == old_form.id
        ).all()

        for old_rule in old_rules:
            cloned_rule = FormApprovalRule(
                form_master_id=new_form.id,  # 🔥 point to new version
                level_no=old_rule.level_no,
                creator_min=old_rule.creator_min,
                creator_max=old_rule.creator_max,
                approve_by_type=old_rule.approve_by_type,
                approve_by_min=old_rule.approve_by_min,
                approve_by_max=old_rule.approve_by_max,
                same_department=old_rule.same_department,
                is_active=old_rule.is_active
            )
            db.add(cloned_rule)

        db.flush()

        # =====================================================
        # 6️⃣ If structure update provided → replace cloned questions
        # =====================================================
        if payload.questions is not None:

            # delete options first
            db.query(FormQuestionOption).filter(
                FormQuestionOption.question_id.in_(
                    db.query(FormQuestion.id).filter(
                        FormQuestion.form_master_id == new_form.id
                    )
                )
            ).delete(synchronize_session=False)

            # delete questions
            db.query(FormQuestion).filter(
                FormQuestion.form_master_id == new_form.id
            ).delete(synchronize_session=False)

            db.flush()

            # recreate from payload
            for q in payload.questions:
                new_q = FormQuestion(
                    form_master_id=new_form.id,
                    question_name=q.question_name,
                    question_label=q.question_label,
                    question_type=q.question_type,
                    is_required=q.is_required,
                    sort_order=q.sort_order
                )

                db.add(new_q)
                db.flush()

                if q.question_type in ["dropdown", "multiselect"] and q.options:
                    for opt in q.options:
                        db.add(FormQuestionOption(
                            question_id=new_q.id,
                            option_value=opt.option_value,
                            option_label=opt.option_label,
                            option_filter=opt.option_filter,
                            sort_order=opt.sort_order
                        ))

        # =====================================================
        # 7️⃣ Commit transaction
        # =====================================================
        db.commit()

        return {
            "message": "New form version created successfully",
            "form_code": new_form.form_code,
            "version": new_form.version,
            "status": new_form.form_status
        }

    except Exception as e:
        db.rollback()
        raise e


# ============================================================
# ACTIVATE LATEST VERSION ONLY
# ============================================================
@router.patch("/master/{form_code}/activate")
def activate_latest_form_version(
    form_code: str,
    db: Session = Depends(get_db)
):
    try:
        # 1️⃣ Get latest version
        latest_form = db.query(FormMaster).filter(
            FormMaster.form_code == form_code,
            FormMaster.is_latest == True
        ).first()

        if not latest_form:
            raise HTTPException(
                status_code=404,
                detail="Latest version not found"
            )

        # 2️⃣ Archive current active version (if exists)
        current_active = db.query(FormMaster).filter(
            FormMaster.form_code == form_code,
            FormMaster.form_status == "Active"
        ).first()

        if current_active and current_active.id != latest_form.id:
            current_active.form_status = "Archived"

        # 3️⃣ Activate latest
        latest_form.form_status = "Active"

        db.commit()

        return {
            "message": "Latest form version activated successfully",
            "form_code": form_code,
            "activated_version": latest_form.version
        }

    except Exception as e:
        db.rollback()
        raise e
