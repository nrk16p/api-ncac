from fastapi import APIRouter, Depends, HTTPException ,Query,BackgroundTasks
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta
from typing import List, Optional
from models.user_model import User ,Position
from services.email_service import send_email,render_form_submit_th ,render_form_done_th
from database import get_db
from models.master_model import (
    FormMaster, FormQuestion, FormSubmission,
    FormSubmissionValue, FormSequence, FormApprovalRule, FormSubmissionLog
)
from models import User, Position
from schemas.form_schema import FormSubmissionCreate, FormResponse, FormValueResponse ,FormSubmissionUpdate
from routes.forms.form_approval_routes import can_user_approve
from zoneinfo import ZoneInfo
router = APIRouter(prefix="/forms", tags=["Forms - Submission"])

# ----------------------------
# LOG: Status Change
# ----------------------------
def log_status_change(
    db: Session,
    submission_id: int,
    old_status: str,
    new_status: str,
    action_by: str,
):
    # ถ้าไม่ได้เปลี่ยนค่า ไม่ต้อง log
    if old_status == new_status:
        return

    log = FormSubmissionLog(
        submission_id=submission_id,
        action="STATUS_CHANGE",
        field_name="status",
        old_value=old_status,
        new_value=new_status,
        action_by=action_by,
    )
    db.add(log)

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

def parse_dt(val):
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(val)
    except Exception:
        return None

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

def get_current_approver_emails(db: Session, submission: FormSubmission) -> list[str]:

    # 🔹 Creator
    creator = db.query(User).filter(
        User.employee_id == submission.created_by
    ).first()

    if not creator:
        return []

    creator_level = get_employee_position_level(db, creator.employee_id)

    # 🔹 Rule for current level
    rule = (
        db.query(FormApprovalRule)
        .filter(
            FormApprovalRule.form_master_id == submission.form_master_id,
            FormApprovalRule.level_no == submission.current_approval_level,
            FormApprovalRule.is_active == True,
            FormApprovalRule.creator_min <= creator_level,
            FormApprovalRule.creator_max >= creator_level,
        )
        .first()
    )

    if not rule:
        return []

    # 🔹 Find users in SAME DEPARTMENT only
    same_dept_users = (
        db.query(User)
        .filter(User.department_id == creator.department_id)
        .all()
    )

    result = []

    for user in same_dept_users:

        level = get_employee_position_level(db, user.employee_id)
        if not level:
            continue

        # position level rule
        if rule.approve_by_type == "position_level":
            if level == rule.approve_by_value:
                result.append(user.email)

        elif rule.approve_by_type == "position_level_range":
            if rule.approve_by_min <= level <= rule.approve_by_max:
                result.append(user.email)

    return list(set(result))

    
# ----------------------------
# Submit
# ----------------------------
@router.post("/submit")
def submit_form(
    payload: FormSubmissionCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):

    form = (
        db.query(FormMaster)
        .filter(
            FormMaster.form_code == payload.form_code,
            FormMaster.is_latest == True,
            FormMaster.form_status == "Active"
        )
        .first()
    )

    if not form:
        raise HTTPException(status_code=404, detail="Active form not found")

    try:

        form_id = generate_form_id(db, form.form_code)

        creator_level = get_employee_position_level(
            db,
            payload.created_by
        )

        if not form.need_approval:
            status_approve, current_level = "Approved", None
        else:
            status_approve, current_level = determine_initial_approval(
                db,
                form.id,
                creator_level
            )

        submission = FormSubmission(
            form_master_id=form.id,
            form_id=form_id,
            created_by=payload.created_by,
            updated_by=payload.updated_by or payload.created_by,
            status="Open",
            status_approve=status_approve,
            current_approval_level=current_level
        )

        db.add(submission)
        db.flush()

        log_status_change(
            db=db,
            submission_id=submission.id,
            old_status=None,
            new_status="Open",
            action_by=payload.created_by
        )

        submitted = {v.question_id: v for v in payload.values}

        for q in form.questions:
            if q.is_required and q.id not in submitted:
                raise HTTPException(
                    400,
                    f"Missing required field: {q.question_label}"
                )

        for v in payload.values:

            q = next((x for x in form.questions if x.id == v.question_id), None)

            if not q:
                raise HTTPException(
                    400,
                    f"Invalid question_id {v.question_id}"
                )

            if q.question_type == "multiselect" and isinstance(v.value_text, list):
                v.value_text = ",".join(v.value_text)

            db.add(FormSubmissionValue(
                submission_id=submission.id,
                question_id=v.question_id,
                value_text=v.value_text,
                value_number=v.value_number,
                value_date=v.value_date,
                value_boolean=v.value_boolean
            ))

        db.commit()
        db.refresh(submission)

        # =====================================================
        # ✉️ SEND EMAIL AFTER SUCCESS
        # =====================================================
        # =====================================================
        # Build TO + CC structure (Single Email)
        # =====================================================

        # 🔹 Get creator FIRST
        creator = db.query(User).filter(
            User.employee_id == submission.created_by
        ).first()

        # 🔹 Load values
        values = (
            db.query(FormSubmissionValue)
            .options(joinedload(FormSubmissionValue.question))
            .filter(FormSubmissionValue.submission_id == submission.id)
            .all()
        )

        fields = []

        for v in values:
            value = (
                v.value_text
                or v.value_number
                or v.value_date
                or ("ใช่" if v.value_boolean else "ไม่ใช่")
                or "-"
            )

            fields.append({
                "label": v.question.question_label,
                "value": value
            })

        # 🔹 Render template AFTER creator exists
        body = render_form_submit_th({
            "form_id": submission.form_id,
            "form_name": form.form_name,
            "created_at": submission.created_at.strftime("%d/%m/%Y %H:%M"),
            "status": submission.status_approve,
            "full_name": f"{creator.firstname} {creator.lastname}" if creator else "-",
            "fields": fields,
            "system_url": f"https://menait-service.vercel.app/mytickets/{submission.form_id}"
        })

        # 🔹 Build TO + CC
        to_email = creator.email if creator else None

        cc_list = []
        cc_list.append("itcenter@menatransport.co.th")

        if submission.current_approval_level:
            approver_emails = get_current_approver_emails(db, submission)
            cc_list.extend(approver_emails)

        cc_list = list(set(cc_list))

        if to_email in cc_list:
            cc_list.remove(to_email)

        subject = f"[IT Service] {submission.form_id}"

        if to_email:
            background_tasks.add_task(
                send_email,
                to_email,
                subject,
                body,
                cc_list
            )
        return {
            "message": "Form submitted",
            "submission_id": submission.id,
            "form_id": submission.form_id,
            "form_master_id": form.id,
            "form_version": form.version,
            "status": submission.status,
            "status_approve": submission.status_approve,
            "current_approval_level": submission.current_approval_level
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------
# 🔹 Update STATUS (Workflow)
# -------------------------
@router.put("/{form_id}/status")
def update_status(
    form_id: str,
    background_tasks: BackgroundTasks,   # 👈 add this
    new_status: str = Query(..., regex="^(Open|In-Progress|Done|Backlog)$"),
    employee_id: str = Query(...),
    db: Session = Depends(get_db),
    ):
    """
    Workflow:
    1. Open         -> create
    2. In-Progress  -> user starts working
    3. Done         -> close
    4. Backlog      -> send back to queue
    """

    # 🔍 หา submission ล่าสุดของ form_id
    submission = (
        db.query(FormSubmission)
        .filter(FormSubmission.form_id == form_id)
        .order_by(FormSubmission.id.desc())
        .first()
    )

    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    try:
        old_status = submission.status
        submission.status = new_status
        submission.updated_by = employee_id

        # 🔹 LOG: only when status changed
        if old_status != new_status:
            log_status_change(
                db=db,
                submission_id=submission.id,   # 👈 ยังใช้ submission_id จริง
                old_status=old_status,
                new_status=new_status,
                action_by=employee_id
            )

        db.commit()
        # =====================================================
        # ✉️ Send Email ONLY when Done
        # =====================================================
        if new_status == "Done":

            creator = db.query(User).filter(
                User.employee_id == submission.created_by
            ).first()

            if creator and creator.email:

                subject = f"[IT Service] {submission.form_id}"

                body = render_form_done_th({
                    "form_id": submission.form_id,
                    "form_name": submission.form.form_name if submission.form else "",
                    "system_url": f"https://menait-service.vercel.app/mytickets/{submission.form_id}"
                })

                background_tasks.add_task(
                    send_email,
                    creator.email,
                    subject,
                    body,
                    ["itcenter@menatransport.co.th"]  # CC IT
                )

                return {
                    "message": "Status updated",
                    "form_id": form_id,
                    "submission_id": submission.id,
                    "old_status": old_status,
                    "new_status": new_status
                }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------
# 🔹 Read
# -------------------------

@router.get("/", response_model=List[FormResponse])
def get_form(
    db: Session = Depends(get_db),
    employee_id: Optional[str] = Query(None),
    form_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):

    # =====================================================
    # 1️⃣ Base Query
    # =====================================================
    query = (
        db.query(FormSubmission)
        .options(
            joinedload(FormSubmission.form),
            joinedload(FormSubmission.values)
                .joinedload(FormSubmissionValue.question),
            joinedload(FormSubmission.approval_logs)
        )
    )

    if employee_id:
        query = query.filter(FormSubmission.created_by == employee_id)

    if form_id:
        query = query.filter(FormSubmission.form_id == form_id)

    if start_date and end_date:
        start = parse_dt(start_date)
        end = parse_dt(end_date)
        if start and end:
            end_next = end + timedelta(days=1)
            query = query.filter(
                FormSubmission.created_at >= start,
                FormSubmission.created_at < end_next
            )

    results = query.all() or []

    # =====================================================
    # 2️⃣ Collect IDs
    # =====================================================
    employee_ids = set()   # for created_by (employee_id)
    user_ids = set()       # for action_by (users.id)

    for sub in results:
        if sub.created_by:
            employee_ids.add(sub.created_by)

        for log in sub.approval_logs or []:
            if log.action_by:
                user_ids.add(log.action_by)

    # =====================================================
    # 3️⃣ Load Users (creator)
    # =====================================================
    users_by_employee = (
        db.query(User)
        .options(
            joinedload(User.department),
            joinedload(User.site),
            joinedload(User.position),
        )
        .filter(User.employee_id.in_(employee_ids))
        .all()
    ) if employee_ids else []

    user_employee_map = {u.employee_id: u for u in users_by_employee}

    # =====================================================
    # 4️⃣ Load Users (approver)
    # =====================================================
    users_by_id = (
        db.query(User)
        .filter(User.id.in_(user_ids))
        .all()
    ) if user_ids else []

    user_id_map = {u.id: u for u in users_by_id}

    # =====================================================
    # 5️⃣ Flatten Response
    # =====================================================
    output = []

    for sub in results:

        # ------------------------
        # Creator
        # ------------------------
        user = user_employee_map.get(sub.created_by)
        department = user.department if user else None
        site = user.site if user else None
        position = user.position if user else None

        # ------------------------
        # Approval
        # ------------------------
        logs_sorted = sorted(
            sub.approval_logs, key=lambda x: x.level_no
        ) if sub.approval_logs else []

        latest_log = logs_sorted[-1] if logs_sorted else None
        latest_action_by = latest_log.action_by if latest_log else None
        latest_actor = user_id_map.get(latest_action_by) if latest_action_by else None

        item = {
            # =============================
            # FORM BASIC
            # =============================
            "form_id": sub.form_id,
            "status": sub.status,
            "status_approve": sub.status_approve,
            "created_by": sub.created_by,
            "created_at": (
                sub.created_at
                .replace(tzinfo=ZoneInfo("UTC"))
                .astimezone(ZoneInfo("Asia/Bangkok"))
                if sub.created_at else None
            ),
            # =============================
            # USER (CREATOR)
            # =============================
            "firstname": user.firstname if user else None,
            "lastname": user.lastname if user else None,
            "email": user.email if user else None,
            "image_url": user.image_url if user else None,

            # =============================
            # DEPARTMENT
            # =============================
            "department_name_th": department.department_name_th if department else None,

            # =============================
            # SITE
            # =============================
            "site_id": site.site_id if site else None,
            "site_code": site.site_code if site else None,
            "site_name_th": site.site_name_th if site else None,
            "site_name_en": site.site_name_en if site else None,

            # =============================
            # POSITION
            # =============================
            "position_id": position.position_id if position else None,
            "position_name_th": position.position_name_th if position else None,
            "position_name_en": position.position_name_en if position else None,

            # =============================
            # FORM META
            # =============================
            "form_type": sub.form.form_type if sub.form else None,
            "form_code": sub.form.form_code if sub.form else None,
            "form_name": sub.form.form_name if sub.form else None,
            "form_master_id": sub.form.id if sub.form else None,
            "form_version": sub.form.version if sub.form else None,
            "form_is_latest": sub.form.is_latest if sub.form else None,

            # =============================
            # APPROVAL (LATEST)
            # =============================
            "remark": latest_log.remark if latest_log else None,
            "action_by_firstname": latest_actor.firstname if latest_actor else None,
            "action_by_lastname": latest_actor.lastname if latest_actor else None,
            "action_at": (
                latest_log.action_at
                .replace(tzinfo=ZoneInfo("UTC"))
                .astimezone(ZoneInfo("Asia/Bangkok"))
                if latest_log and latest_log.action_at else None
            ),          
            # =============================
            # APPROVAL LOGS
            # =============================
            "approval_logs": [
                {
                    "level_no": log.level_no,
                    "action": log.action,
                    "remark": log.remark,
                    "action_by_employee_id": log.action_by,
                    "action_at": log.action_at,
                }
                for log in logs_sorted
            ],

            # =============================
            # VALUES
            # =============================
            "values": [
                {
                    "question_id": v.question_id,
                    "value_text": v.value_text,
                    "value_number": v.value_number,
                    "value_date": v.value_date,
                    "value_boolean": v.value_boolean,
                    "question_name": v.question.question_name if v.question else None,
                    "question_label": v.question.question_label if v.question else None,
                    "question_type": v.question.question_type if v.question else None,
                }
                for v in sub.values
            ],
        }

        output.append(item)

    return output

@router.get("/{form_id}/logs")
def get_logs_by_form_id_path(
    form_id: str,
    db: Session = Depends(get_db),
):
    logs = (
        db.query(FormSubmissionLog)
        .join(FormSubmission, FormSubmission.id == FormSubmissionLog.submission_id)
        .filter(FormSubmission.form_id == form_id)
        .order_by(FormSubmissionLog.action_at.asc())
        .all()
    )

    return [
        {
            "form_id": form_id,
            "action": log.action,
            "field_name": log.field_name,
            "old_value": log.old_value,
            "new_value": log.new_value,
            "action_by": log.action_by,
            "action_at": log.action_at,
        }
        for log in logs
    ]
@router.put("/{form_id}")
def update_form_details(
    form_id: str,
    payload: FormSubmissionUpdate,
    db: Session = Depends(get_db),
):
    submission = (
        db.query(FormSubmission)
        .options(
            joinedload(FormSubmission.values),
            joinedload(FormSubmission.form)
                .joinedload(FormMaster.questions)
        )
        .filter(FormSubmission.form_id == form_id)
        .first()
    )

    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    # 🔒 lock done
    if submission.status == "Done":
        raise HTTPException(status_code=400, detail="Cannot edit completed form")

    try:
        submission.updated_by = payload.updated_by

        # 🔥 get question set from SAME VERSION only
        question_map = {q.id: q for q in submission.form.questions}

        existing = {v.question_id: v for v in submission.values}

        for v in payload.values:

            q = question_map.get(v.question_id)

            if not q:
                raise HTTPException(
                    400,
                    f"Invalid question_id {v.question_id} for this form version"
                )

            # validation
            if q.is_required and not any([
                v.value_text,
                v.value_number,
                v.value_date,
                v.value_boolean
            ]):
                raise HTTPException(
                    400,
                    f"{q.question_label} is required"
                )

            if q.question_type == "multiselect" and isinstance(v.value_text, list):
                v.value_text = ",".join(v.value_text)

            if v.question_id in existing:
                rec = existing[v.question_id]

                if rec.value_text != v.value_text:
                    db.add(FormSubmissionLog(
                        submission_id=submission.id,
                        action="UPDATE_VALUE",
                        field_name=q.question_name,
                        old_value=str(rec.value_text),
                        new_value=str(v.value_text),
                        action_by=payload.updated_by
                    ))

                rec.value_text = v.value_text
                rec.value_number = v.value_number
                rec.value_date = v.value_date
                rec.value_boolean = v.value_boolean

            else:
                db.add(FormSubmissionValue(
                    submission_id=submission.id,
                    question_id=v.question_id,
                    value_text=v.value_text,
                    value_number=v.value_number,
                    value_date=v.value_date,
                    value_boolean=v.value_boolean
                ))

        db.commit()

        return {
            "message": "Form details updated",
            "form_id": submission.form_id,
            "submission_id": submission.id,
            "form_master_id": submission.form_master_id,
            "form_version": submission.form.version
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))