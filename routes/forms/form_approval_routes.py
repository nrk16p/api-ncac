from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from database import get_db
from fastapi import BackgroundTasks
from services.email_service import render_form_rejected_th ,  send_email , render_form_approved_th

from models.master_model import (
    FormSubmission,
    FormApprovalRule,
    FormApprovalLog,
    FormMaster,
)
from models import User, Position
from models.form_approver_department import FormApproverDepartment
from schemas.form_approver_schema import ApproverDepartmentCreate

router = APIRouter(prefix="/forms", tags=["Forms - Approval"])


# ============================================================
# Helpers
# ============================================================

def get_user_by_employee_id(db: Session, employee_id: str) -> User | None:
    return db.query(User).filter(User.employee_id == employee_id).first()


def get_employee_position_level(db: Session, employee_id: str) -> int | None:
    user = get_user_by_employee_id(db, employee_id)
    if not user or not user.position_id:
        return None

    pos = db.query(Position).filter(Position.position_id == user.position_id).first()
    return pos.position_level_id if pos else None


def get_applicable_rule(
    db: Session,
    form_master_id: int,
    creator_level: int,
    level_no: int,
) -> FormApprovalRule | None:
    return (
        db.query(FormApprovalRule)
        .filter(
            FormApprovalRule.form_master_id == form_master_id,
            FormApprovalRule.level_no == level_no,
            FormApprovalRule.is_active == True,
            FormApprovalRule.creator_min <= creator_level,
            FormApprovalRule.creator_max >= creator_level,
        )
        .first()
    )


def approver_can_handle_department(
    db: Session,
    employee_id: str,
    department_id: int,
) -> bool:
    return (
        db.query(FormApproverDepartment)
        .filter(
            FormApproverDepartment.employee_id == employee_id,
            FormApproverDepartment.department_id == department_id,
            FormApproverDepartment.is_active == True,
        )
        .first()
        is not None
    )


def can_user_approve(
    *,
    db: Session,
    rule: FormApprovalRule,
    approver: User,
    approver_level: int,
    requester: User,
) -> bool:
    # level check
    if rule.approve_by_type == "position_level":
        if approver_level != rule.approve_by_value:
            return False

    elif rule.approve_by_type == "position_level_range":
        if not (rule.approve_by_min <= approver_level <= rule.approve_by_max):
            return False

    # department logic
    if rule.same_department:
        return requester.department_id == approver.department_id

    # new responsibility logic
    return approver_can_handle_department(
        db=db,
        employee_id=approver.employee_id,
        department_id=requester.department_id,
    )


# ============================================================
# 🔎 Pending Approvals
# ============================================================

@router.get("/pending-approvals")
def get_pending_approvals(
    employee_id: str = Query(...),
    db: Session = Depends(get_db),
):
    approver = get_user_by_employee_id(db, employee_id)
    if not approver:
        return []

    approver_level = get_employee_position_level(db, employee_id)
    if not approver_level:
        return []

    submissions = (
        db.query(FormSubmission)
        .filter(FormSubmission.status_approve == "In Progress")
        .all()
    )

    result = []

    for sub in submissions:
        requester = get_user_by_employee_id(db, sub.created_by)
        if not requester or not requester.position_id:
            continue

        pos_req = db.query(Position).filter(
            Position.position_id == requester.position_id
        ).first()
        if not pos_req:
            continue

        rule = get_applicable_rule(
            db=db,
            form_master_id=sub.form_master_id,
            creator_level=pos_req.position_level_id,
            level_no=sub.current_approval_level,
        )
        if not rule:
            continue

        if can_user_approve(
            db=db,
            rule=rule,
            approver=approver,
            approver_level=approver_level,
            requester=requester,
        ):
            result.append({
                "submission_id": sub.id,
                "form_id": sub.form_id,
                "form_code": sub.form.form_code,
                "form_name": sub.form.form_name,
                "current_level": sub.current_approval_level,
                "status": sub.status_approve,
                "created_by": sub.created_by,
                "created_at": sub.created_at,
                "firstname": requester.firstname,
                "lastname": requester.lastname,
                "email": requester.email,
                "image_url": requester.image_url,
                
            })

    return result


# ============================================================
# ✅ Approve
# ============================================================

@router.post("/{form_id}/approve")
def approve_submission(
    form_id: str,
    background_tasks: BackgroundTasks,   # 👈 add here
    employee_id: str = Query(...),
    remark: str | None = None,
    db: Session = Depends(get_db),
):
    submission = db.query(FormSubmission).filter(
        FormSubmission.form_id == form_id
    ).first()
    if not submission:
        raise HTTPException(404, "Submission not found")

    if submission.status_approve != "In Progress":
        raise HTTPException(400, "Submission not in approvable state")

    requester = get_user_by_employee_id(db, submission.created_by)
    approver = get_user_by_employee_id(db, employee_id)
    if not requester or not approver:
        raise HTTPException(404, "User not found")

    approver_level = get_employee_position_level(db, employee_id)
    if not approver_level:
        raise HTTPException(403, "Approver has no position level")

    pos_req = db.query(Position).filter(
        Position.position_id == requester.position_id
    ).first()
    if not pos_req:
        raise HTTPException(400, "Requester position not found")

    rule = get_applicable_rule(
        db=db,
        form_master_id=submission.form_master_id,
        creator_level=pos_req.position_level_id,
        level_no=submission.current_approval_level,
    )
    if not rule:
        raise HTTPException(400, "Approval rule not found")

    if not can_user_approve(
        db=db,
        rule=rule,
        approver=approver,
        approver_level=approver_level,
        requester=requester,
    ):
        raise HTTPException(403, "Not authorized to approve")

    db.add(FormApprovalLog(
        submission_id=submission.id,
        level_no=submission.current_approval_level,
        action="APPROVED",
        action_by=approver.id,
        remark=remark,
    ))

    next_rule = get_applicable_rule(
        db=db,
        form_master_id=submission.form_master_id,
        creator_level=pos_req.position_level_id,
        level_no=submission.current_approval_level + 1,
    )

    if next_rule:
        submission.current_approval_level += 1
        submission.status_approve = "In Progress"
    else:
        submission.status_approve = "Approved"

    db.commit()
    if submission.status_approve == "Approved":

        creator = db.query(User).filter(
            User.employee_id == submission.created_by
        ).first()

        if creator and creator.email:

            body = render_form_approved_th({
                "form_id": submission.form_id,
                "form_name": submission.form.form_name if submission.form else "",
                "system_url": f"https://menait-service.vercel.app/mytickets/{submission.form_id}"
            })

            background_tasks.add_task(
                send_email,
                creator.email,
                f"[แบบฟอร์ม] {submission.form_id}",
                body,
                ["itcenter@menatransport.co.th"]
            )
    return {
        "message": "Approved successfully",
        "form_id": submission.form_id,
        "status": submission.status_approve,
        "current_level": submission.current_approval_level,
    }


# ============================================================
# ❌ Reject
# ============================================================

@router.post("/{form_id}/reject")
def reject_submission(
    form_id: str,
    background_tasks: BackgroundTasks,   # 👈 add here

    employee_id: str = Query(...),
    remark: str = Query(...),
    db: Session = Depends(get_db),
):
    submission = db.query(FormSubmission).filter(
        FormSubmission.form_id == form_id
    ).first()
    if not submission:
        raise HTTPException(404, "Submission not found")

    if submission.status_approve != "In Progress":
        raise HTTPException(400, "Submission not in approvable state")

    requester = get_user_by_employee_id(db, submission.created_by)
    approver = get_user_by_employee_id(db, employee_id)
    if not requester or not approver:
        raise HTTPException(404, "User not found")

    approver_level = get_employee_position_level(db, employee_id)
    if not approver_level:
        raise HTTPException(403, "Approver has no position level")

    pos_req = db.query(Position).filter(
        Position.position_id == requester.position_id
    ).first()
    if not pos_req:
        raise HTTPException(400, "Requester position not found")

    rule = get_applicable_rule(
        db=db,
        form_master_id=submission.form_master_id,
        creator_level=pos_req.position_level_id,
        level_no=submission.current_approval_level,
    )
    if not rule:
        raise HTTPException(400, "Approval rule not found")

    if not can_user_approve(
        db=db,
        rule=rule,
        approver=approver,
        approver_level=approver_level,
        requester=requester,
    ):
        raise HTTPException(403, "Not authorized to reject")

    db.add(FormApprovalLog(
        submission_id=submission.id,
        level_no=submission.current_approval_level,
        action="REJECTED",
        action_by=approver.id,
        remark=remark,
    ))

    submission.status_approve = "Rejected"
    db.commit()
    creator = db.query(User).filter(
        User.employee_id == submission.created_by
    ).first()

    if creator and creator.email:

        body = render_form_rejected_th({
            "form_id": submission.form_id,
            "form_name": submission.form.form_name if submission.form else "",
            "remark": remark,
            "system_url": f"https://menait-service.vercel.app/mytickets/{submission.form_id}"
        })

        background_tasks.add_task(
            send_email,
            creator.email,
            f"[แบบฟอร์ม] {submission.form_id}",
            body,
            ["itcenter@menatransport.co.th"]
        )
    return {
        "message": "Rejected successfully",
        "form_id": submission.form_id,
        "status": submission.status_approve,
    }


# ============================================================
# 🛠 Approver Responsibility (Admin)
# ============================================================

@router.post("/approvers/departments")
def assign_departments(
    payload: ApproverDepartmentCreate,
    db: Session = Depends(get_db),
):
    dept_ids = set(payload.department_ids)

    for dept_id in dept_ids:
        row = db.query(FormApproverDepartment).filter(
            FormApproverDepartment.employee_id == payload.employee_id,
            FormApproverDepartment.department_id == dept_id,
        ).first()

        if not row:
            db.add(FormApproverDepartment(
                employee_id=payload.employee_id,
                department_id=dept_id,
                is_active=True,
            ))
        else:
            row.is_active = True

    db.commit()

    return {
        "message": "Departments assigned",
        "employee_id": payload.employee_id,
        "department_ids": list(dept_ids),
    }


@router.get("/approvers/{employee_id}/departments")
def get_departments_by_employee(
    employee_id: str,
    db: Session = Depends(get_db),
):
    rows = db.query(FormApproverDepartment).filter(
        FormApproverDepartment.employee_id == employee_id,
        FormApproverDepartment.is_active == True,
    ).all()

    return {
        "employee_id": employee_id,
        "departments": [r.department_id for r in rows],
    }


@router.delete("/approvers/{employee_id}/departments/{department_id}")
def remove_department(
    employee_id: str,
    department_id: int,
    db: Session = Depends(get_db),
):
    row = db.query(FormApproverDepartment).filter(
        FormApproverDepartment.employee_id == employee_id,
        FormApproverDepartment.department_id == department_id,
    ).first()

    if not row:
        raise HTTPException(404, "Mapping not found")

    row.is_active = False
    db.commit()

    return {
        "message": "Department removed",
        "employee_id": employee_id,
        "department_id": department_id,
    }
