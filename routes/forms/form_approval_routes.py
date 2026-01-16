from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from database import get_db
from models.master_model import FormSubmission, FormApprovalRule, FormApprovalLog
from models import User, Position

router = APIRouter(prefix="/forms", tags=["Forms - Approval"])


# ============================================================
# Helper: employee_id -> position_level_id
# ============================================================
def get_employee_position_level(db: Session, employee_id: str) -> int | None:
    user = db.query(User).filter(User.employee_id == employee_id).first()
    if not user or not user.position_id:
        return None

    pos = db.query(Position).filter(Position.position_id == user.position_id).first()
    if not pos:
        return None

    return pos.position_level_id


# ============================================================
# Helper: employee_id -> User (PK)
# ============================================================
def get_user_by_employee_id(db: Session, employee_id: str) -> User | None:
    return db.query(User).filter(User.employee_id == employee_id).first()


# ============================================================
# Helper: หา rule ที่ใช้ได้ตาม creator_level + level_no
# ============================================================
def get_applicable_rule(
    db: Session,
    form_master_id: int,
    creator_level: int,
    level_no: int
) -> FormApprovalRule | None:
    return (
        db.query(FormApprovalRule)
        .filter(
            FormApprovalRule.form_master_id == form_master_id,
            FormApprovalRule.level_no == level_no,
            FormApprovalRule.is_active == True,
            FormApprovalRule.creator_min <= creator_level,
            FormApprovalRule.creator_max >= creator_level
        )
        .first()
    )


# ============================================================
# 🔎 Pending Approvals
# ============================================================
@router.get("/pending-approvals")
def get_pending_approvals(
    employee_id: str = Query(...),
    db: Session = Depends(get_db)
):
    user_level = get_employee_position_level(db, employee_id)
    if not user_level:
        return []

    submissions = (
        db.query(FormSubmission)
        .filter(FormSubmission.status_approve == "In Progress")
        .all()
    )

    result = []

    for sub in submissions:
        # หา requester
        requester = db.query(User).filter(User.employee_id == sub.created_by).first()
        if not requester or not requester.position_id:
            continue

        pos_req = db.query(Position).filter(Position.position_id == requester.position_id).first()
        if not pos_req:
            continue

        creator_level = pos_req.position_level_id

        # หา rule ที่ตรงกับ creator + current level
        rule = get_applicable_rule(
            db=db,
            form_master_id=sub.form_master_id,
            creator_level=creator_level,
            level_no=sub.current_approval_level
        )
        if not rule:
            continue

        can_approve = False

        if rule.approve_by_type == "position_level_range":
            if rule.approve_by_min <= user_level <= rule.approve_by_max:
                if rule.same_department:
                    approver = get_user_by_employee_id(db, employee_id)
                    if requester and approver and requester.department_id == approver.department_id:
                        can_approve = True
                else:
                    can_approve = True

        elif rule.approve_by_type == "position_level":
            if user_level == rule.approve_by_value:
                if rule.same_department:
                    approver = get_user_by_employee_id(db, employee_id)
                    if requester and approver and requester.department_id == approver.department_id:
                        can_approve = True
                else:
                    can_approve = True

        if can_approve:
            result.append({
                "submission_id": sub.id,
                "form_id": sub.form_id,
                "form_code": sub.form.form_code,
                "current_level": sub.current_approval_level,
                "status": sub.status_approve,
                "created_by": sub.created_by,
                "created_at": sub.created_at
            })

    return result


# ============================================================
# ✅ Approve
# ============================================================
@router.post("/{form_id}/approve")
def approve_submission(
    form_id: str,                      # ⬅ เปลี่ยนเป็น form_id
    employee_id: str = Query(...),
    remark: str | None = None,
    db: Session = Depends(get_db)
):
    submission = (
        db.query(FormSubmission)
        .filter(FormSubmission.form_id == form_id)
        .first()
    )
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    if submission.status_approve != "In Progress":
        raise HTTPException(status_code=400, detail="Submission is not in approvable state")

    # หา requester
    requester = db.query(User).filter(User.employee_id == submission.created_by).first()
    if not requester or not requester.position_id:
        raise HTTPException(status_code=400, detail="Requester has no position")

    pos_req = db.query(Position).filter(Position.position_id == requester.position_id).first()
    if not pos_req:
        raise HTTPException(status_code=400, detail="Requester position not found")

    creator_level = pos_req.position_level_id

    rule = get_applicable_rule(
        db=db,
        form_master_id=submission.form_master_id,
        creator_level=creator_level,
        level_no=submission.current_approval_level
    )
    if not rule:
        raise HTTPException(status_code=400, detail="Approval rule not found")

    approver = get_user_by_employee_id(db, employee_id)
    if not approver:
        raise HTTPException(status_code=404, detail="Approver not found")

    user_level = get_employee_position_level(db, employee_id)
    if not user_level:
        raise HTTPException(status_code=403, detail="User has no position level")

    can_approve = False

    if rule.approve_by_type == "position_level":
        if user_level == rule.approve_by_value:
            if rule.same_department:
                if requester.department_id == approver.department_id:
                    can_approve = True
            else:
                can_approve = True

    elif rule.approve_by_type == "position_level_range":
        if rule.approve_by_min <= user_level <= rule.approve_by_max:
            if rule.same_department:
                if requester.department_id == approver.department_id:
                    can_approve = True
            else:
                can_approve = True

    if not can_approve:
        raise HTTPException(status_code=403, detail="You are not authorized to approve this submission")

    # 📝 บันทึก log
    log = FormApprovalLog(
        submission_id=submission.id,   # ยังใช้ PK ภายใน DB เหมือนเดิม
        level_no=submission.current_approval_level,
        action="APPROVED",
        action_by=approver.id,
        remark=remark
    )
    db.add(log)

    next_rule = get_applicable_rule(
        db=db,
        form_master_id=submission.form_master_id,
        creator_level=creator_level,
        level_no=submission.current_approval_level + 1
    )

    if next_rule:
        submission.current_approval_level += 1
        submission.status_approve = "In Progress"
    else:
        submission.status_approve = "Approved"

    db.commit()

    return {
        "message": "Approved successfully",
        "form_id": submission.form_id,
        "status": submission.status_approve,
        "current_level": submission.current_approval_level
    }
# ============================================================
# ❌ Reject
# ============================================================
@router.post("/{form_id}/reject")
def reject_submission(
    form_id: str,                      # ⬅ เปลี่ยนเป็น form_id
    employee_id: str = Query(...),
    remark: str = Query(...),
    db: Session = Depends(get_db)
):
    submission = (
        db.query(FormSubmission)
        .filter(FormSubmission.form_id == form_id)
        .first()
    )
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    if submission.status_approve != "In Progress":
        raise HTTPException(status_code=400, detail="Submission is not in approvable state")

    requester = db.query(User).filter(User.employee_id == submission.created_by).first()
    if not requester or not requester.position_id:
        raise HTTPException(status_code=400, detail="Requester has no position")

    pos_req = db.query(Position).filter(Position.position_id == requester.position_id).first()
    if not pos_req:
        raise HTTPException(status_code=400, detail="Requester position not found")

    creator_level = pos_req.position_level_id

    rule = get_applicable_rule(
        db=db,
        form_master_id=submission.form_master_id,
        creator_level=creator_level,
        level_no=submission.current_approval_level
    )
    if not rule:
        raise HTTPException(status_code=400, detail="Approval rule not found")

    approver = get_user_by_employee_id(db, employee_id)
    if not approver:
        raise HTTPException(status_code=404, detail="Approver not found")

    user_level = get_employee_position_level(db, employee_id)
    if not user_level:
        raise HTTPException(status_code=403, detail="User has no position level")

    can_reject = False

    if rule.approve_by_type == "position_level":
        if user_level == rule.approve_by_value:
            if rule.same_department:
                if requester.department_id == approver.department_id:
                    can_reject = True
            else:
                can_reject = True

    elif rule.approve_by_type == "position_level_range":
        if rule.approve_by_min <= user_level <= rule.approve_by_max:
            if rule.same_department:
                if requester.department_id == approver.department_id:
                    can_reject = True
            else:
                can_reject = True

    if not can_reject:
        raise HTTPException(status_code=403, detail="You are not authorized to reject this submission")

    log = FormApprovalLog(
        submission_id=submission.id,
        level_no=submission.current_approval_level,
        action="REJECTED",
        action_by=approver.id,
        remark=remark
    )
    db.add(log)

    submission.status_approve = "Rejected"
    db.commit()

    return {
        "message": "Rejected successfully",
        "form_id": submission.form_id,
        "status": submission.status_approve
    }
# ============================================================
# 📜 Get Approval Logs (filter by employee_id)
# ============================================================
# ============================================================
# 📜 Get Approval Logs (filter by employee_id) + form_id
# ============================================================
@router.get("/approve-logs")
def get_approval_logs(
    employee_id: str | None = Query(None),
    db: Session = Depends(get_db)
):
    # join กับ users และ submissions เพื่อเอา form_id
    query = (
        db.query(FormApprovalLog, User, FormSubmission)
        .join(User, FormApprovalLog.action_by == User.id)
        .join(FormSubmission, FormApprovalLog.submission_id == FormSubmission.id)
    )

    if employee_id:
        query = query.filter(User.employee_id == employee_id)

    rows = query.order_by(FormApprovalLog.id.desc()).all()

    return [
        {
            "id": log.id,
            "submission_id": log.submission_id,
            "form_id": submission.form_id,     # ✅ เพิ่ม form_id
            "level_no": log.level_no,
            "action": log.action,
            "user_id": user.id,
            "employee_id": user.employee_id,
            "remark": log.remark,
            "action_at": log.action_at
        }
        for log, user, submission in rows
    ]
