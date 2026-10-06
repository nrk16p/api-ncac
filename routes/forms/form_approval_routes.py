from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, selectinload
from database import get_db
from fastapi import BackgroundTasks
from services.email_service import render_form_rejected_th ,  send_email , render_form_approved_th
from services.notify_guard import ADVANCE_FORM_TYPE, notifications_enabled
from services.finance import approval_repo
from services.finance import finance_mail as fin_mail

from models.master_model import (
    FormSubmission,
    FormApprovalRule,
    FormApprovalLog,
    FormMaster,
)
from models import User, Position, Department
from models.form_approver_department import FormApproverDepartment
from schemas.form_approver_schema import ApproverDepartmentCreate
from datetime import datetime, timedelta

router = APIRouter(prefix="/forms", tags=["Forms - Approval"])


# ============================================================
# Helpers
# ============================================================

def parse_dt(val):
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(val)
    except Exception:
        return None


def is_advance_submission(submission: FormSubmission) -> bool:
    return submission.form is not None and submission.form.form_type == ADVANCE_FORM_TYPE


# ------------------------------------------------------------
# Per-request cache
#
# One FastAPI request = one SQLAlchemy Session (see database.get_db,
# which opens/closes a fresh Session per request), so Session.info is a
# safe place to stash a request-scoped cache: it can never leak into
# another request, and nothing in approve/reject mutates these tables
# mid-request (they only write FormSubmission/FormApprovalLog), so the
# cache can't go stale within the lifetime of one request either.
#
# This replaces what used to be N+1 / N*M per-row queries (one
# get_user_by_employee_id + one Position lookup + a full "all active
# users" rescan per FormSubmission row, each doing its own queries) with
# a handful of bulk loads done at most once per request.
# ------------------------------------------------------------

_CACHE_KEY = "_approval_cache"


def _get_request_cache(db: Session) -> dict:
    cache = db.info.get(_CACHE_KEY)
    if cache is None:
        cache = {
            "users_by_employee_id": None,
            "active_users": None,
            "positions_level_by_id": None,
            "departments_by_id": None,
            "approver_dept_pairs": None,
            "rules_by_form_level": None,
            "eligible_approver_memo": {},
        }
        db.info[_CACHE_KEY] = cache
    return cache


def _load_users(db: Session, cache: dict) -> dict:
    """Populates + returns users_by_employee_id; also fills active_users.

    Loaded ordered by id ascending so that, if duplicate employee_id rows
    ever exist, the first one (lowest id) wins — approximating the
    previous `.filter(...).first()` behavior without relying on
    Postgres's unspecified default row order.
    """
    if cache["users_by_employee_id"] is None:
        by_emp: dict[str, User] = {}
        active: list[User] = []
        for u in db.query(User).order_by(User.id.asc()).all():
            if u.employee_id is not None and u.employee_id not in by_emp:
                by_emp[u.employee_id] = u
            if u.employee_status == "Active":
                active.append(u)
        cache["users_by_employee_id"] = by_emp
        cache["active_users"] = active
    return cache["users_by_employee_id"]


def _load_position_levels(db: Session, cache: dict) -> dict:
    if cache["positions_level_by_id"] is None:
        cache["positions_level_by_id"] = {
            p.position_id: p.position_level_id for p in db.query(Position).all()
        }
    return cache["positions_level_by_id"]


def _load_departments(db: Session, cache: dict) -> dict:
    if cache["departments_by_id"] is None:
        cache["departments_by_id"] = {
            d.department_id: d for d in db.query(Department).all()
        }
    return cache["departments_by_id"]


def _load_approver_dept_pairs(db: Session, cache: dict) -> set:
    if cache["approver_dept_pairs"] is None:
        cache["approver_dept_pairs"] = {
            (r.employee_id, r.department_id)
            for r in db.query(FormApproverDepartment)
            .filter(FormApproverDepartment.is_active == True)
            .all()
        }
    return cache["approver_dept_pairs"]


def _load_rules_by_form_level(db: Session, cache: dict) -> dict:
    """All active rules grouped by (form_master_id, level_no), each list
    ordered by id ascending.

    Used both for the fallback candidate scan (same order as the old
    explicit `.order_by(FormApprovalRule.id.asc())` query) and, in
    get_applicable_rule below, to approximate the old creator-range
    `.first()` query — which had **no** ORDER BY, so Postgres was free to
    return any matching row. We pick the lowest-id match deterministically;
    this only differs from the old behavior if a form/level ever has more
    than one rule whose creator_min/max band contains the same
    creator_level (an overlapping-rule misconfiguration), in which case
    the old code's choice was itself unspecified.
    """
    if cache["rules_by_form_level"] is None:
        grouped: dict[tuple[int, int], list[FormApprovalRule]] = {}
        for r in (
            db.query(FormApprovalRule)
            .filter(FormApprovalRule.is_active == True)
            .order_by(FormApprovalRule.id.asc())
            .all()
        ):
            grouped.setdefault((r.form_master_id, r.level_no), []).append(r)
        cache["rules_by_form_level"] = grouped
    return cache["rules_by_form_level"]


def get_user_by_employee_id(db: Session, employee_id: str | None) -> User | None:
    if not employee_id:
        return None
    cache = _get_request_cache(db)
    return _load_users(db, cache).get(employee_id)


def get_employee_position_level(db: Session, employee_id: str) -> int | None:
    user = get_user_by_employee_id(db, employee_id)
    if not user or not user.position_id:
        return None

    cache = _get_request_cache(db)
    return _load_position_levels(db, cache).get(user.position_id)


def get_applicable_rule(
    db: Session,
    form_master_id: int,
    creator_level: int,
    level_no: int,
) -> FormApprovalRule | None:
    cache = _get_request_cache(db)
    rules = _load_rules_by_form_level(db, cache).get((form_master_id, level_no), [])
    for r in rules:
        if r.creator_min <= creator_level <= r.creator_max:
            return r
    return None


def _rule_has_eligible_approver(
    db: Session,
    rule: FormApprovalRule,
    requester: User,
) -> bool:
    cache = _get_request_cache(db)
    memo_key = (rule.id, requester.department_id)
    memo = cache["eligible_approver_memo"]
    if memo_key in memo:
        return memo[memo_key]

    _load_users(db, cache)  # ensures active_users is populated
    positions = _load_position_levels(db, cache)
    active_users = cache["active_users"]

    result = False
    for user in active_users:
        approver_level = positions.get(user.position_id) if user.position_id else None
        if not approver_level:
            continue

        if can_user_approve(
            db=db,
            rule=rule,
            approver=user,
            approver_level=approver_level,
            requester=requester,
        ):
            result = True
            break

    memo[memo_key] = result
    return result


def get_applicable_rule_with_fallback(
    db: Session,
    form_master_id: int,
    creator_level: int,
    level_no: int,
    requester: User,
) -> FormApprovalRule | None:
    rule = get_applicable_rule(db, form_master_id, creator_level, level_no)

    if rule and _rule_has_eligible_approver(db, rule, requester):
        return rule

    cache = _get_request_cache(db)
    candidates = _load_rules_by_form_level(db, cache).get((form_master_id, level_no), [])

    for candidate in candidates:
        if rule and candidate.id == rule.id:
            continue
        if _rule_has_eligible_approver(db, candidate, requester):
            return candidate

    return None


def approver_can_handle_department(
    db: Session,
    employee_id: str,
    department_id: int,
) -> bool:
    cache = _get_request_cache(db)
    pairs = _load_approver_dept_pairs(db, cache)
    return (employee_id, department_id) in pairs


def can_user_approve(
    *,
    db: Session,
    rule: FormApprovalRule,
    approver: User,
    approver_level: int,
    requester: User,
) -> bool:

    # --------------------------------
    # Position Level Rule
    # --------------------------------
    if rule.approve_by_type == "position_level":
        if approver_level != rule.approve_by_value:
            return False

    elif rule.approve_by_type == "position_level_range":
        if not (rule.approve_by_min <= approver_level <= rule.approve_by_max):
            return False

    # --------------------------------
    # Department Logic
    # --------------------------------

    # 1️⃣ Same department first
    if requester.department_id == approver.department_id:
        return True

    # 2️⃣ Cross department via responsibility table
    if approver_can_handle_department(
        db=db,
        employee_id=approver.employee_id,
        department_id=requester.department_id,
    ):
        return True

    return False


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

    cache = _get_request_cache(db)
    positions = _load_position_levels(db, cache)

    submissions = (
        db.query(FormSubmission)
        .options(selectinload(FormSubmission.form))
        .filter(FormSubmission.status_approve == "In Progress")
        .all()
    )

    result = []

    for sub in submissions:
        if is_advance_submission(sub): continue  # ADV has its own queue: GET /finance/approvals/pending
        requester = get_user_by_employee_id(db, sub.created_by)
        if not requester or not requester.position_id:
            continue

        creator_level = positions.get(requester.position_id)
        if creator_level is None:
            continue

        rule = get_applicable_rule_with_fallback(
            db=db,
            form_master_id=sub.form_master_id,
            creator_level=creator_level,
            level_no=sub.current_approval_level,
            requester=requester,
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
# 🕘 Approval History (งานที่อนุมัติ/ปฏิเสธไปแล้ว)
# ============================================================

def _history_item(log, sub, approver, requester, department):
    return {
        "submission_id": sub.id,
        "form_id": sub.form_id,
        "form_code": sub.form.form_code,
        "form_name": sub.form.form_name,
        "current_level": sub.current_approval_level,
        "level_no": log.level_no,
        "status": sub.status,
        "status_approve": "Approved" if log.action == "APPROVED" else "Rejected",
        "submission_status_approve": sub.status_approve,
        "action": log.action,
        "action_at": log.action_at,
        "action_by_firstname": approver.firstname,
        "action_by_lastname": approver.lastname,
        "remark": log.remark,
        "created_by": sub.created_by,
        "created_at": sub.created_at,
        "firstname": requester.firstname if requester else None,
        "lastname": requester.lastname if requester else None,
        "email": requester.email if requester else None,
        "image_url": requester.image_url if requester else None,
        "department_name_th": department.department_name_th if department else None,
    }


def _history_filters(query, approver, start_date, end_date):
    query = query.filter(
        FormApprovalLog.action_by == approver.id,
        FormApprovalLog.action.in_(["APPROVED", "REJECTED"]),
    )
    if start_date and end_date:
        start = parse_dt(start_date)
        end = parse_dt(end_date)
        if start and end:
            query = query.filter(
                FormApprovalLog.action_at >= start,
                FormApprovalLog.action_at < end + timedelta(days=1),
            )
    return query


def latest_log_per_submission(rows):
    """rows: (log_id, submission_id) already in history order → the log ids kept, one per submission (the first
    seen = the approver's latest action)."""
    seen, kept = set(), []
    for log_id, submission_id in rows:
        if submission_id in seen:
            continue
        seen.add(submission_id)
        kept.append(log_id)
    return kept


def users_by_employee_ids(db: Session, employee_ids) -> dict:
    """ONE query for many employee_ids → {employee_id: User}; the lowest id wins on a duplicate (as _load_users)."""
    ids = {e for e in employee_ids if e}
    if not ids:
        return {}
    found: dict[str, User] = {}
    for u in db.query(User).filter(User.employee_id.in_(ids)).order_by(User.id.asc()).all():
        found.setdefault(u.employee_id, u)
    return found


def _approval_history_page(db, employee_id, start_date, end_date, page, page_size, scope="all"):
    approver = users_by_employee_ids(db, [employee_id]).get(employee_id)
    if not approver:
        return {"items": [], "total": 0, "page": page, "page_size": page_size}

    # light ordered scan (ids only) → dedupe → slice; only the page's rows are loaded in full
    order = (FormApprovalLog.action_at.desc(), FormApprovalLog.id.asc())
    light_q = _history_filters(
        db.query(FormApprovalLog.id, FormApprovalLog.submission_id), approver, start_date, end_date)
    if scope in ("advance", "it"):
        light_q = (light_q.join(FormSubmission, FormApprovalLog.submission_id == FormSubmission.id)
                   .join(FormMaster, FormMaster.id == FormSubmission.form_master_id))
        light_q = light_q.filter(FormMaster.form_type == ADVANCE_FORM_TYPE if scope == "advance"
                                 else FormMaster.form_type != ADVANCE_FORM_TYPE)
    light = light_q.order_by(*order).all()
    kept = latest_log_per_submission((r[0], r[1]) for r in light)
    page_ids = kept[(page - 1) * page_size: page * page_size]

    full = {}
    if page_ids:
        for log, sub in (
            db.query(FormApprovalLog, FormSubmission)
            .join(FormSubmission, FormApprovalLog.submission_id == FormSubmission.id)
            .options(selectinload(FormSubmission.form))
            .filter(FormApprovalLog.id.in_(page_ids))
            .all()
        ):
            full[log.id] = (log, sub)
    rows = [full[i] for i in page_ids if i in full]

    requesters = users_by_employee_ids(db, [sub.created_by for _, sub in rows])
    departments = _load_departments(db, _get_request_cache(db)) if rows else {}
    items = []
    for log, sub in rows:
        requester = requesters.get(sub.created_by)
        department = departments.get(requester.department_id) if requester and requester.department_id else None
        items.append(_history_item(log, sub, approver, requester, department))
    return {"items": items, "total": len(kept), "page": page, "page_size": page_size}


@router.get("/approval-history")
def get_approval_history(
    employee_id: str = Query(...),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    page: int | None = Query(None, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    scope: str = Query("all", pattern="^(advance|it|all)$"),
    db: Session = Depends(get_db),
):
    """`scope` (paged mode only): advance = form_type Advance (is_advance_submission), it = others, all; applied in
    SQL before paging. Without `page`: the full array (IT and ADV, unchanged). With `page` (1-based): {items, total, page,
    page_size}; page < 1 or page_size outside 1..200 → 422."""
    if isinstance(page, int):  # (a direct call without the arg leaves the Query default object here)
        return _approval_history_page(db, employee_id, start_date, end_date, page,
                                      page_size if isinstance(page_size, int) else 20,
                                      scope if isinstance(scope, str) else "all")

    approver = get_user_by_employee_id(db, employee_id)
    if not approver:
        return []

    query = _history_filters(
        db.query(FormApprovalLog, FormSubmission)
        .join(FormSubmission, FormApprovalLog.submission_id == FormSubmission.id)
        .options(selectinload(FormSubmission.form)),
        approver, start_date, end_date,
    )

    # newest first; on a tie (an ADV dynamic skip writes the approver's own step log and then a system step-2 log in
    # one transaction, so both share action_at) the earlier log — the approver's own — comes first and is kept below
    rows = query.order_by(FormApprovalLog.action_at.desc(), FormApprovalLog.id.asc()).all()

    cache = _get_request_cache(db)
    departments = _load_departments(db, cache)

    result = []
    seen_submissions = set()

    for log, sub in rows:
        # one row per submission — keep only the latest action of this approver
        if sub.id in seen_submissions:
            continue
        seen_submissions.add(sub.id)

        requester = get_user_by_employee_id(db, sub.created_by)
        department = None
        if requester and requester.department_id:
            department = departments.get(requester.department_id)
        result.append(_history_item(log, sub, approver, requester, department))

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

    advance = is_advance_submission(submission)
    if advance:
        # two-step chain (v3 §5): lock the row so concurrent approvals of the same step serialize
        db.refresh(submission, with_for_update=True)
        if submission.status_approve != "In Progress":
            raise HTTPException(400, "Submission not in approvable state")
        decision = approval_repo.approval_decision(db, submission, employee_id)
        if not decision["allowed"]:
            raise HTTPException(403, "Not authorized to approve")
    else:
        approver_level = get_employee_position_level(db, employee_id)
        if not approver_level:
            raise HTTPException(403, "Approver has no position level")

        pos_req = db.query(Position).filter(
            Position.position_id == requester.position_id
        ).first()
        if not pos_req:
            raise HTTPException(400, "Requester position not found")

        rule = get_applicable_rule_with_fallback(
            db=db,
            form_master_id=submission.form_master_id,
            creator_level=pos_req.position_level_id,
            level_no=submission.current_approval_level,
            requester=requester,
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

    if advance:
        # log level_no = current step; finish (Approved) or move to step 2 (stays In Progress)
        approval_repo.record_approval(db, submission, approver.id, decision, remark)
    else:
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
    if advance:  # finance email (off unless FINANCE_EMAIL_ENABLED): next-step approvers, or the requester
        fin_mail.queue_event(
            background_tasks,
            db,
            fin_mail.APPROVED if submission.status_approve == "Approved" else fin_mail.STEP_PENDING,
            submission,
        )
    if submission.status_approve == "Approved" and notifications_enabled(submission.form):

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
                f"[IT Service] {submission.form_id}",
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

    advance = is_advance_submission(submission)
    level_no = submission.current_approval_level
    if advance:
        # same eligibility as approve, at the current step (v3 §5): a reject at any step → Rejected
        db.refresh(submission, with_for_update=True)
        if submission.status_approve != "In Progress":
            raise HTTPException(400, "Submission not in approvable state")
        decision = approval_repo.approval_decision(db, submission, employee_id)
        if not decision["allowed"]:
            raise HTTPException(403, "Not authorized to reject")
        level_no = decision["step"]
    else:
        approver_level = get_employee_position_level(db, employee_id)
        if not approver_level:
            raise HTTPException(403, "Approver has no position level")

        pos_req = db.query(Position).filter(
            Position.position_id == requester.position_id
        ).first()
        if not pos_req:
            raise HTTPException(400, "Requester position not found")

        rule = get_applicable_rule_with_fallback(
            db=db,
            form_master_id=submission.form_master_id,
            creator_level=pos_req.position_level_id,
            level_no=submission.current_approval_level,
            requester=requester,
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
        level_no=level_no,
        action="REJECTED",
        action_by=approver.id,
        remark=remark,
    ))

    submission.status_approve = "Rejected"
    db.commit()
    if advance:
        fin_mail.queue_event(background_tasks, db, fin_mail.REJECTED, submission, remark=remark)
    creator = db.query(User).filter(
        User.employee_id == submission.created_by
    ).first()

    if creator and creator.email and notifications_enabled(submission.form):

        body = render_form_rejected_th({
            "form_id": submission.form_id,
            "form_name": submission.form.form_name if submission.form else "",
            "remark": remark,
            "system_url": f"https://menait-service.vercel.app/mytickets/{submission.form_id}"
        })

        background_tasks.add_task(
            send_email,
            creator.email,
            f"[IT Service] {submission.form_id}",
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
