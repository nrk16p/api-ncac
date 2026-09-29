"""DB loading for ADV approval by amount. Rules live in approval_logic (pure)."""
from __future__ import annotations

from dataclasses import dataclass, field

from models.finance_model import FinApprovalTier
from models.form_approver_department import FormApproverDepartment
from models.master_model import FormMaster, FormSubmission
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


def load_context(db) -> ApprovalContext:
    return ApprovalContext(tiers=load_tiers(db), people=load_people(db), mappings=load_mappings(db))


def describe(db, requester_employee_id, amount, ctx: ApprovalContext | None = None):
    ctx = ctx or load_context(db)
    return rules.evaluate(ctx.tiers, ctx.people, ctx.mappings, requester_employee_id, amount)


def _amount_of(db, submission_id):
    return (advance_repo.request_values_by_submission(db, [submission_id]).get(submission_id) or {}).get("amount")


def can_approve_submission(db, submission, approver_employee_id) -> bool:
    ctx = load_context(db)
    try:
        result = describe(db, submission.created_by, _amount_of(db, submission.id), ctx)
    except AdvanceRuleError:
        return False
    approver = ctx.people.get(approver_employee_id)
    requester = ctx.people.get(submission.created_by)
    if approver is None or requester is None:
        return False
    return rules.can_approve(approver, requester, result["required_level"],
                             ctx.mappings.get(approver_employee_id, ()))


def pending_for(db, employee_id):
    ctx = load_context(db)
    approver = ctx.people.get(employee_id)
    if approver is None or approver["level"] is None:
        return []
    subs = (
        db.query(FormSubmission)
        .join(FormMaster, FormMaster.id == FormSubmission.form_master_id)
        .filter(FormMaster.form_type == advance_repo.ADVANCE_FORM_TYPE,
                FormSubmission.status_approve == "In Progress")
        .order_by(FormSubmission.id.desc())
        .all()
    )
    requests = advance_repo.request_values_by_submission(db, [s.id for s in subs])
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
        if not rules.can_approve(approver, requester, result["required_level"], ctx.mappings.get(employee_id, ())):
            continue
        items.append({
            "form_id": sub.form_id,
            "submission_id": sub.id,
            "created_at": advance_repo._iso(sub.created_at),
            "requester": people_info.get(sub.created_by) or {
                "employee_id": sub.created_by, "name": None, "department": None, "site": None, "site_code": None},
            "request": advance_repo.serialize_request(request),
            "tier": {"clause": result["clause"], "approver_label": result["approver_label"],
                     "required_level": result["required_level"]},
            "tab": rules.approval_tab(approver["level"], result["direct_level"]),
        })
    return items
