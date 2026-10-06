"""DB loading for ADV approval by amount. Rules live in approval_logic (pure)."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from models.finance_model import FinApprovalTier
from models.form_approver_department import FormApproverDepartment
from models.master_model import FormApprovalLog, FormMaster, FormSubmission
from models.user_model import Position, User
from services.finance import advance_repo
from services.finance import approval_logic as rules
from services.finance.advance_logic import AdvanceRuleError


@dataclass
class ApprovalContext:
    tiers: list = field(default_factory=list)
    people: dict = field(default_factory=dict)
    mappings: dict = field(default_factory=dict)


def load_tiers(db):
    return [
        {"clause": t.clause, "amount_max": t.amount_max, "min_level": t.min_level,
         "approver_label": t.approver_label, "sort_order": t.sort_order}
        for t in db.query(FinApprovalTier).order_by(FinApprovalTier.sort_order).all()
    ]


def list_tiers(db):
    return [{**t, "amount_max": float(t["amount_max"]) if t["amount_max"] is not None else None}
            for t in load_tiers(db)]


def load_people(db):
    rows = db.query(User, Position.position_level_id).outerjoin(
        Position, Position.position_id == User.position_id).all()
    return {
        user.employee_id: {"employee_id": user.employee_id, "id": user.id, "level": level,
                           "department_id": user.department_id, "active": user.employee_status == "Active"}
        for user, level in rows if user.employee_id
    }


def load_mappings(db):
    mappings = {}
    rows = db.query(FormApproverDepartment).filter(FormApproverDepartment.is_active == True).all()  # noqa: E712
    for row in rows:
        mappings.setdefault(row.employee_id, set()).add(row.department_id)
    return mappings


# v3 §9: users/tiers/mappings change rarely, so the context is cached in-process for 60 s (level and department
# changes apply within a minute). One global entry (no key); the lock guards the swap, loading happens outside it.
CONTEXT_TTL_SECONDS = 60
_clock = time.monotonic
_ctx_lock = threading.Lock()
_ctx_cache = None  # (expires_at, ApprovalContext)


def clear_context_cache():
    global _ctx_cache
    with _ctx_lock:
        _ctx_cache = None


def load_context(db) -> ApprovalContext:
    global _ctx_cache
    with _ctx_lock:
        hit = _ctx_cache
    if hit is not None and _clock() < hit[0]:
        return hit[1]
    ctx = ApprovalContext(tiers=load_tiers(db), people=load_people(db), mappings=load_mappings(db))
    with _ctx_lock:
        _ctx_cache = (_clock() + CONTEXT_TTL_SECONDS, ctx)
    return ctx


def describe(db, requester_employee_id, amount, ctx: ApprovalContext | None = None):
    """v2 keys (clause, approver_label, min_level, required_level = final step, direct_level) + steps."""
    ctx = ctx or load_context(db)
    return rules.evaluate(ctx.tiers, ctx.people, ctx.mappings, requester_employee_id, amount)


def _amount_of(db, submission_id):
    return (advance_repo.request_values_by_submission(db, [submission_id]).get(submission_id) or {}).get("amount")


# ---------------------------- approval rounds ----------------------------

def _log_dict(log):
    return {"id": log.id, "level_no": log.level_no, "action": log.action, "action_by": log.action_by,
            "action_at": log.action_at, "remark": log.remark}


def round_logs_by_submission(db, submission_ids) -> dict:
    """{submission_id: current-round logs} in one query."""
    ids = [i for i in submission_ids if i is not None]
    if not ids:
        return {}
    grouped = {}
    rows = (db.query(FormApprovalLog).filter(FormApprovalLog.submission_id.in_(ids))
            .order_by(FormApprovalLog.id.asc()).all())
    for row in rows:
        grouped.setdefault(row.submission_id, []).append(_log_dict(row))
    return {sid: rules.current_round(logs) for sid, logs in grouped.items()}


def current_round_logs(db, submission_id) -> list:
    """form_approval_logs after the latest RESUBMITTED marker, or all of them when there is none."""
    return round_logs_by_submission(db, [submission_id]).get(submission_id, [])


# ---------------------------- current step ----------------------------

def approval_decision(db, submission, approver_employee_id) -> dict:
    """Eligibility for the submission's current step + what an approval would do (see rules.decide)."""
    ctx = load_context(db)
    try:
        info = describe(db, submission.created_by, _amount_of(db, submission.id), ctx)
    except AdvanceRuleError:
        return {"allowed": False}
    return rules.decide(info, ctx.people, ctx.mappings, submission.created_by, approver_employee_id,
                        submission.current_approval_level, current_round_logs(db, submission.id))


def can_approve_submission(db, submission, approver_employee_id) -> bool:
    return approval_decision(db, submission, approver_employee_id)["allowed"]


def record_approval(db, submission, approver_user_id, decision, remark=None):
    """Log the current step (level_no = step); a dynamic skip also logs each step it satisfied.
    Then finish (Approved) or move to the next step (stays In Progress)."""
    step = decision["step"]
    submission.current_approval_level = step
    db.add(FormApprovalLog(submission_id=submission.id, level_no=step, action=rules.ACTION_APPROVED,
                           action_by=approver_user_id, remark=remark))
    by_step = {s["step"]: s for s in decision["steps"]}
    for skipped in decision["skipped"]:
        db.add(FormApprovalLog(
            submission_id=submission.id, level_no=skipped, action=rules.ACTION_APPROVED,
            action_by=approver_user_id,
            remark=rules.skip_remark(skipped, step, decision["approver_level"], by_step[skipped]["required_level"])))
    if decision["outcome"] == rules.OUTCOME_APPROVED:
        submission.status_approve = "Approved"
    else:
        submission.current_approval_level = decision["next_step"]
        submission.status_approve = "In Progress"


def _users_by_id(db, user_ids):
    ids = {i for i in user_ids if i is not None}
    if not ids:
        return {}
    return {u.id: u for u in db.query(User).filter(User.id.in_(ids)).all()}


def detail_approval(db, submission, amount) -> dict:
    """The detail `approval` block: v2 keys + steps, current_step, step_approvals (current round)."""
    ctx = load_context(db)
    info = describe(db, submission.created_by, amount, ctx)
    logs = current_round_logs(db, submission.id)
    state = rules.evaluate_step(info, ctx.people, ctx.mappings, submission.created_by,
                                submission.current_approval_level, logs)
    approvals = rules.step_approvals(logs)
    users = _users_by_id(db, [a["action_by"] for a in approvals])
    step_approvals = []
    for approval in approvals:
        user = users.get(approval["action_by"])
        step_approvals.append({
            "step": approval["step"],
            "employee_id": user.employee_id if user is not None else None,
            "name": advance_repo._full_name(user),
            "action_at": advance_repo._iso(approval["action_at"]),
        })
    return {"clause": info["clause"], "approver_label": info["approver_label"],
            "required_level": info["required_level"], "steps": info["steps"],
            "current_step": state["step"], "step_approvals": step_approvals}


def suggested_approvers(db, submission) -> dict:
    """The lowest eligible approvers of the submission's current step (share link)."""
    ctx = load_context(db)
    requester_id = submission.created_by
    info = describe(db, requester_id, _amount_of(db, submission.id), ctx)
    requester = ctx.people[requester_id]
    state = rules.evaluate_step(info, ctx.people, ctx.mappings, requester_id, submission.current_approval_level,
                                current_round_logs(db, submission.id))
    direct = rules.direct_approvers(ctx.people.values(), requester, state["required_level"], ctx.mappings,
                                    state["excluded"])
    details = advance_repo.people_by_employee_id(db, [p["employee_id"] for p in direct])
    approvers = []
    for p in direct:
        d = details.get(p["employee_id"]) or {}
        approvers.append({"employee_id": p["employee_id"], "name": d.get("name"),
                          "position": d.get("position"), "department": d.get("department")})
    approvers.sort(key=lambda a: (a["name"] or "", a["employee_id"]))
    return {"requester_employee_id": requester_id, "clause": info["clause"],
            "approver_label": info["approver_label"], "required_level": info["required_level"],
            "steps": info["steps"], "step": state["step"], "total_steps": state["total_steps"],
            "step_required_level": state["required_level"], "step_label": state["label"],
            "approvers": approvers}


def _in_progress_advances(db):
    return (
        db.query(FormSubmission)
        .join(FormMaster, FormMaster.id == FormSubmission.form_master_id)
        .filter(FormMaster.form_type == advance_repo.ADVANCE_FORM_TYPE,
                FormSubmission.status_approve == "In Progress")
        .order_by(FormSubmission.id.desc())
        .all()
    )


def pending_for(db, employee_id):
    """ADV In Progress items whose *current* step this user can approve; tab per step."""
    ctx = load_context(db)
    approver = ctx.people.get(employee_id)
    if approver is None or approver["level"] is None:
        return []
    subs = _in_progress_advances(db)
    ids = [s.id for s in subs]
    requests = advance_repo.request_values_by_submission(db, ids)
    rounds = round_logs_by_submission(db, ids)
    people_info = advance_repo.people_by_employee_id(db, [s.created_by for s in subs])
    items = []
    for sub in subs:
        requester = ctx.people.get(sub.created_by)
        if requester is None:
            continue
        request = requests.get(sub.id) or {}
        try:
            result = describe(db, sub.created_by, request.get("amount"), ctx)
        except AdvanceRuleError:
            continue
        state = rules.evaluate_step(result, ctx.people, ctx.mappings, sub.created_by, sub.current_approval_level,
                                    rounds.get(sub.id, []))
        if not rules.can_approve(approver, requester, state["required_level"], ctx.mappings.get(employee_id, ()),
                                 state["excluded"]):
            continue
        items.append({
            "form_id": sub.form_id,
            "submission_id": sub.id,
            "created_at": advance_repo._iso(sub.created_at),
            "requester": people_info.get(sub.created_by) or {
                "employee_id": sub.created_by, "name": None, "department": None, "site": None, "site_code": None,
                "position": None},
            "request": advance_repo.serialize_request(request),
            "tier": {"clause": result["clause"], "approver_label": result["approver_label"],
                     "required_level": result["required_level"]},
            "step": state["step"],
            "total_steps": state["total_steps"],
            "step_required_level": state["required_level"],
            "step_label": state["label"],
            "tab": rules.approval_tab(approver["level"], state["direct_level"]),
        })
    return items
