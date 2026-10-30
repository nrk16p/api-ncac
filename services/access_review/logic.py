"""Access Review (ทบทวนสิทธิ์ผู้ใช้) — rules + user-facing use cases.

Pure rules first (level rank, approval routing, status guards), then the use cases the routes call.
Use cases commit their own transaction; they raise ArError (mapped to HTTP by the router).

Routing on submit (by the requester's position_levels.level_name):
  Lv.1–4 (or unknown) → Active Lv.5/6 users in the same division (route dept_head)
                        → else form_approver_divisions of the division (route division) → else it_queue
  Lv.5–6              → form_approver_divisions of the division (route division) → else it_queue
  Lv.7+               → bypass: approved immediately by the requester
The requester is never an approver. Any one approver's decision finalises the review; IT admins decide it_queue.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from models.access_review_model import ArCycle, ArReview, ArReviewLine, ArRoleItem
from models.user_model import User
from services.access_review import mail, repo

# ---------------------------- constants ----------------------------

LEVEL_RANK: Dict[str, int] = {
    "chief executive officer": 9,
    "c-level": 8,
    "deputy c-level": 7,
    "senior manager": 6,
    "manager": 5,
    "assistant manager": 4,
    "supervisor": 3,
    "assistant supervisor": 2,
    "officer": 1,
}

DRAFT, PENDING, APPROVED, REJECTED = "draft", "pending", "approved", "rejected"
EDITABLE_STATUSES = (DRAFT, REJECTED)
ROUTE_DEPT_HEAD, ROUTE_DIVISION, ROUTE_IT_QUEUE, ROUTE_BYPASS = "dept_head", "division", "it_queue", "bypass"
KEEP, REMOVE = "keep", "remove"
NOTE_MAX = 1000

MSG_LOGIN_REQUIRED = "กรุณาเข้าสู่ระบบ"
MSG_NO_CYCLE = "ยังไม่มีรอบการทบทวนสิทธิ์"
MSG_CYCLE_CLOSED = "รอบการทบทวนสิทธิ์ปิดแล้ว"
MSG_NO_ROLES = "ไม่พบสิทธิ์ที่ต้องทบทวน"
MSG_READ_ONLY = "แบบทบทวนนี้ส่งแล้ว แก้ไขไม่ได้"
MSG_UNKNOWN_LINE = "ไม่พบสิทธิ์รายการที่ระบุ (user_role_id {id})"
MSG_FOREIGN_ITEM = "รายการสิทธิ์ {item_id} ไม่อยู่ในกลุ่มสิทธิ์ของ user_role_id {id}"
REQUESTER_NOTE_MAX = 2000
IT_QUEUE_LABEL = "ผู้ดูแลระบบ IT"
MSG_NOT_FOUND = "ไม่พบแบบทบทวนสิทธิ์"
MSG_FORBIDDEN_VIEW = "ไม่มีสิทธิ์ดูแบบทบทวนนี้"
MSG_FORBIDDEN_DECIDE = "คุณไม่ใช่ผู้อนุมัติของแบบทบทวนนี้"
MSG_NOT_PENDING = "แบบทบทวนนี้ไม่ได้อยู่ระหว่างรออนุมัติ"
MSG_REJECT_COMMENT = "กรุณาระบุเหตุผลที่ไม่อนุมัติ"
MSG_ROLE_NOT_FOUND = "ไม่พบกลุ่มสิทธิ์"


class ArError(Exception):
    http_status = 400

    def __init__(self, message: str, http_status: Optional[int] = None):
        super().__init__(message)
        self.message = message
        if http_status is not None:
            self.http_status = http_status


class ArForbidden(ArError):
    http_status = 403


class ArNotFound(ArError):
    http_status = 404


class ArConflict(ArError):
    http_status = 409


@dataclass
class Caller:
    user: User
    employee_id: str
    is_admin: bool = False


@dataclass
class RoutePlan:
    route: str
    approvers: List[str] = field(default_factory=list)


# ---------------------------- pure rules ----------------------------

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)  # naive timestamps are UTC
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def level_rank(level_name: Optional[str]) -> Optional[int]:
    if not level_name:
        return None
    return LEVEL_RANK.get(level_name.strip().lower())


def plan_route(requester_id: str, rank: Optional[int], division_people: Sequence[Tuple[str, Optional[int]]],
               division_approvers: Sequence[str]) -> RoutePlan:
    """division_people: (employee_id, rank) of Active users in the requester's division.
    division_approvers: active form_approver_divisions employee_ids for that division."""
    if rank is not None and rank >= 7:
        return RoutePlan(ROUTE_BYPASS, [])
    fallback = [e for e in dict.fromkeys(division_approvers) if e and e != requester_id]
    if rank in (5, 6):
        return RoutePlan(ROUTE_DIVISION, fallback) if fallback else RoutePlan(ROUTE_IT_QUEUE, [])
    heads = [e for e, r in division_people if r in (5, 6) and e and e != requester_id]
    heads = list(dict.fromkeys(heads))
    if heads:
        return RoutePlan(ROUTE_DEPT_HEAD, heads)
    if fallback:
        return RoutePlan(ROUTE_DIVISION, fallback)
    return RoutePlan(ROUTE_IT_QUEUE, [])


def ensure_cycle_open(cycle: Optional[ArCycle]) -> ArCycle:
    if cycle is None:
        raise ArError(MSG_NO_CYCLE)
    if not cycle.is_open:
        raise ArError(MSG_CYCLE_CLOSED)
    return cycle


def ensure_editable(status: str) -> None:
    if status not in EDITABLE_STATUSES:
        raise ArConflict(MSG_READ_ONLY)


def derive_decision(active_item_ids: set, unchecked: set, requested: Optional[str]) -> str:
    """A role with items: "remove" only when every item is unticked. An item-less role (Email group): the
    requested decision, default "keep" (everything starts ticked)."""
    if active_item_ids:
        return REMOVE if active_item_ids <= unchecked else KEEP
    return REMOVE if requested == REMOVE else KEEP


def clean_note(note: Optional[str], limit: int = NOTE_MAX) -> Optional[str]:
    text = (note or "").strip()
    return text[:limit] or None


def can_decide(review: ArReview, approver_ids: Iterable[str], caller: Caller) -> bool:
    if review.status != PENDING or caller.employee_id == review.employee_id:
        return False
    if caller.employee_id in set(approver_ids):
        return True
    return review.route == ROUTE_IT_QUEUE and caller.is_admin


def can_view(review: ArReview, approver_ids: Iterable[str], caller: Caller) -> bool:
    return caller.is_admin or caller.employee_id == review.employee_id or caller.employee_id in set(approver_ids)


# ---------------------------- people helpers ----------------------------

def full_name(user: Optional[User]) -> Optional[str]:
    if user is None:
        return None
    name = " ".join(p for p in (user.firstname, user.lastname) if p).strip()
    return name or user.username


def user_level_name(user: User) -> Optional[str]:
    pos = user.position
    return pos.level.level_name if pos is not None and pos.level is not None else None


def user_division_id(user: User) -> Optional[int]:
    return user.department.division_id if user.department is not None else None


def person(user: Optional[User], employee_id: Optional[str] = None) -> dict:
    if user is None:
        return {"employee_id": employee_id, "name": employee_id, "position": None, "department": None,
                "division": None, "image_url": None}
    dept = user.department
    div = dept.division if dept is not None else None
    return {
        "employee_id": user.employee_id,
        "name": full_name(user),
        "position": user.position.position_name_th if user.position is not None else None,
        "department": dept.department_name_th if dept is not None else None,
        "division": (div.division_th or div.division_eng) if div is not None else None,
        "image_url": user.image_url,
    }


def compute_plan(db: Session, user: User) -> RoutePlan:
    rank = level_rank(user_level_name(user))
    division_id = user_division_id(user)
    people: List[Tuple[str, Optional[int]]] = []
    if rank is None or rank <= 4:
        people = [(u.employee_id, level_rank(user_level_name(u))) for u in repo.active_users_in_division(db, division_id)]
    approvers: List[str] = []
    if rank is None or rank <= 6:
        approvers = repo.division_approver_ids(db, division_id)
    return plan_route(user.employee_id, rank, people, approvers)


# ---------------------------- serialisers ----------------------------

def account_name(user_role, owner: Optional[User]) -> Optional[str]:
    if user_role.account_name:
        return user_role.account_name
    return owner.email if owner is not None else None


def cycle_dict(cycle: Optional[ArCycle]) -> Optional[dict]:
    if cycle is None:
        return None
    return {"id": cycle.id, "name": cycle.name, "start_date": iso(cycle.start_date), "end_date": iso(cycle.end_date),
            "is_open": bool(cycle.is_open)}


def _review_summary(db: Session, review: Optional[ArReview]) -> Optional[dict]:
    if review is None:
        return None
    decider = repo.get_user(db, review.decided_by) if review.decided_by else None
    return {
        "id": review.id, "status": review.status, "route": review.route, "submitted_at": iso(review.submitted_at),
        "decided_by": review.decided_by, "decided_by_name": full_name(decider) if decider else review.decided_by,
        "decided_at": iso(review.decided_at), "comment": review.comment,
        "requester_note": review.requester_note,
    }


def _approver_people(db: Session, employee_ids: List[str], with_email: bool) -> List[dict]:
    users = repo.users_by_employee_ids(db, employee_ids)
    out = []
    for eid in employee_ids:
        u = users.get(eid)
        item = {"employee_id": eid, "name": full_name(u) or eid,
                "position": u.position.position_name_th if u is not None and u.position is not None else None}
        if with_email:
            item["email"] = u.email if u is not None else None
        out.append(item)
    return out


# ---------------------------- use cases: me ----------------------------

_UNSET = object()


def _scope(db: Session, employee_id: str, review: Optional[ArReview]) -> List[repo.Assignment]:
    """Rows the requester reviews: the stored lines once submitted, otherwise the active user roles."""
    if review is not None and review.status in (PENDING, APPROVED):
        return repo.assignments_by_ids(db, [line.user_role_id for line in repo.review_lines(db, review.id)])
    return repo.active_assignments(db, employee_id)


def count_pending_for(db: Session, caller: Caller) -> int:
    return len(repo.pending_reviews_for(db, caller.employee_id, caller.is_admin))


def next_approver_names(review: Optional[ArReview], approver_ids: List[str], users: Dict[str, User]) -> List[str]:
    """Who decides next (อนุมัติลำดับถัดไป) — only while pending."""
    if review is None or review.status != PENDING:
        return []
    if review.route == ROUTE_IT_QUEUE:
        return [IT_QUEUE_LABEL]
    return [full_name(users.get(e)) or e for e in approver_ids]


def _lines_with_unchecked(db: Session, review: Optional[ArReview]
                          ) -> Tuple[Dict[int, ArReviewLine], Dict[int, List[int]]]:
    """(user_role_id → line, line_id → unticked item ids)."""
    if review is None:
        return {}, {}
    lines = repo.review_lines(db, review.id)
    return {line.user_role_id: line for line in lines}, repo.unchecked_by_line(db, [line.id for line in lines])


def build_me(db: Session, caller: Caller) -> dict:
    cycle = repo.current_cycle(db)
    review = repo.get_review(db, cycle.id, caller.employee_id) if cycle is not None else None
    rows = _scope(db, caller.employee_id, review)
    lines, unchecked = _lines_with_unchecked(db, review)
    counts = repo.item_counts(db, [role.id for _, role, _ in rows])

    systems: Dict[int, dict] = {}
    for user_role, role, system in rows:
        sys_entry = systems.setdefault(system.id, {"code": system.code, "name": system.name, "roles": []})
        line = lines.get(user_role.id)
        sys_entry["roles"].append({
            "user_role_id": user_role.id,
            "account_name": account_name(user_role, caller.user),
            "role_id": role.id,
            "role_name": role.name,
            "item_count": counts.get(role.id, 0),
            "unchecked_item_ids": unchecked.get(line.id, []) if line else [],
            "decision": (line.decision or KEEP) if line else KEEP,
            "note": line.note if line else None,
        })

    approver_ids = [a.employee_id for a in repo.approver_rows(db, review.id)] if review else []
    if review is not None and review.status in (PENDING, APPROVED):
        route_preview, preview_ids = review.route, approver_ids
    elif systems:
        plan = compute_plan(db, caller.user)
        route_preview, preview_ids = plan.route, plan.approvers
    else:
        route_preview, preview_ids = None, []

    summary = _review_summary(db, review)
    if summary is not None:
        summary["next_approver_names"] = next_approver_names(
            review, approver_ids, repo.users_by_employee_ids(db, approver_ids))
    return {
        "is_admin": caller.is_admin,
        "has_pending_approvals": count_pending_for(db, caller),
        "cycle": cycle_dict(cycle),
        "review": summary,
        "requester_note": review.requester_note if review else None,
        "systems": list(systems.values()),
        "approvers_preview": _approver_people(db, preview_ids, with_email=True),
        "route_preview": route_preview,
    }


def item_sort_key(item: ArRoleItem):
    """Same order as repo.items_for_roles (Postgres sorts NULLs last)."""
    return (item.category or "", item.item is None, item.item or "", item.permission is None, item.permission or "",
            item.id)


def item_dict(item: ArRoleItem) -> dict:
    return {"id": item.id, "category": item.category, "item": item.item, "permission": item.permission,
            "is_active": bool(item.is_active)}


def _snapshots(db: Session, lines: Dict[int, ArReviewLine]) -> Dict[int, List[int]]:
    """user_role_id → item ids the role had at submit (lines submitted before snapshots existed are left out)."""
    by_line = repo.snapshot_by_line(db, [line.id for line in lines.values()])
    return {ur_id: by_line[line.id] for ur_id, line in lines.items() if line.id in by_line}


def _items_payload(db: Session, rows: List[repo.Assignment], unchecked_ids: Iterable[int],
                   snapshots: Optional[Dict[int, List[int]]] = None) -> dict:
    """A role with a snapshot shows exactly the items it had at submit; any other role shows its live items."""
    snapshots = snapshots or {}
    live = repo.items_for_roles(db, [role.id for ur, role, _ in rows if ur.id not in snapshots],
                                include_ids=unchecked_ids)
    fixed = repo.items_by_ids(db, [i for ids in snapshots.values() for i in ids])

    def items_of(ur, role):
        if ur.id in snapshots:
            return sorted((fixed[i] for i in snapshots[ur.id] if i in fixed), key=item_sort_key)
        return live.get(role.id, [])

    return {"roles": [{
        "user_role_id": ur.id, "role_id": role.id, "role_name": role.name, "system_code": system.code,
        "items": [item_dict(i) for i in items_of(ur, role)],
    } for ur, role, system in rows]}


def my_items(db: Session, caller: Caller) -> dict:
    cycle = repo.current_cycle(db)
    review = repo.get_review(db, cycle.id, caller.employee_id) if cycle is not None else None
    lines, unchecked = _lines_with_unchecked(db, review)
    submitted = review is not None and review.status in (PENDING, APPROVED)
    return _items_payload(db, _scope(db, caller.employee_id, review),
                          [i for ids in unchecked.values() for i in ids],
                          _snapshots(db, lines) if submitted else None)


def review_items(db: Session, caller: Caller, review_id: int) -> dict:
    review, _ = _load_visible(db, caller, review_id)
    lines, unchecked = _lines_with_unchecked(db, review)
    rows = repo.assignments_by_ids(db, list(lines))
    snapshots = _snapshots(db, lines) if review.status != DRAFT else None
    return _items_payload(db, rows, [i for ids in unchecked.values() for i in ids], snapshots)


def _save_lines(db: Session, caller: Caller, lines: Optional[List[dict]], requester_note=_UNSET
                ) -> Tuple[ArReview, Dict[int, ArReviewLine], List[repo.Assignment]]:
    """Creates/updates the caller's draft for the open cycle (flush only).
    lines: [{user_role_id, unchecked_item_ids, decision?, note?}] — a listed line's unticked set is replaced."""
    cycle = ensure_cycle_open(repo.current_cycle(db))
    active = repo.active_assignments(db, caller.employee_id)
    if not active:
        raise ArError(MSG_NO_ROLES)
    role_of = {ur.id: role.id for ur, role, _ in active}

    review = repo.get_review(db, cycle.id, caller.employee_id)
    if review is None:
        review = ArReview(cycle_id=cycle.id, employee_id=caller.employee_id, status=DRAFT, created_at=utcnow())
        db.add(review)
        db.flush()
    ensure_editable(review.status)
    if requester_note is not _UNSET:
        review.requester_note = clean_note(requester_note, REQUESTER_NOTE_MAX)

    existing = {line.user_role_id: line for line in repo.review_lines(db, review.id)}
    for stale_id in [i for i in existing if i not in role_of]:
        repo.delete_line(db, existing.pop(stale_id))

    if lines:
        active_items = repo.active_item_ids_by_role(db, role_of.values())
        for payload in lines:
            ur_id = payload["user_role_id"]
            if ur_id not in role_of:
                raise ArError(MSG_UNKNOWN_LINE.format(id=ur_id))
            role_items = active_items.get(role_of[ur_id], set())
            unchecked = set(payload.get("unchecked_item_ids") or [])
            foreign = sorted(unchecked - role_items)
            if foreign:
                raise ArError(MSG_FOREIGN_ITEM.format(item_id=foreign[0], id=ur_id))
            line = existing.get(ur_id)
            if line is None:
                line = ArReviewLine(review_id=review.id, user_role_id=ur_id)
                db.add(line)
                db.flush()
                existing[ur_id] = line
            line.decision = derive_decision(role_items, unchecked, payload.get("decision"))
            if "note" in payload:
                line.note = clean_note(payload.get("note"))
            repo.set_unchecked(db, line.id, unchecked)
    db.flush()
    return review, existing, active


def save_lines(db: Session, caller: Caller, lines: List[dict], requester_note=_UNSET) -> dict:
    _save_lines(db, caller, lines, requester_note)
    db.commit()
    return build_me(db, caller)


def _normalise_for_submit(db: Session, review: ArReview, existing: Dict[int, ArReviewLine],
                          active: List[repo.Assignment]) -> None:
    """Every active role gets a line (untouched = all ticked → keep); unticked items that are no longer active
    are dropped and decisions re-derived, so what is submitted matches the current role items. Each line also
    snapshots the role's items ("before"), so the review keeps showing what the requester saw."""
    for ur, _, _ in active:
        if ur.id not in existing:
            line = ArReviewLine(review_id=review.id, user_role_id=ur.id, decision=KEEP)
            db.add(line)
            existing[ur.id] = line
    db.flush()
    role_of = {ur.id: role.id for ur, role, _ in active}
    active_items = repo.active_item_ids_by_role(db, role_of.values())
    unchecked = repo.unchecked_by_line(db, [line.id for line in existing.values()])
    for ur_id, line in existing.items():
        role_items = active_items.get(role_of[ur_id], set())
        stored = set(unchecked.get(line.id, []))
        kept = stored & role_items
        if kept != stored:
            repo.set_unchecked(db, line.id, kept)
        repo.set_snapshot(db, line.id, role_items)
        line.decision = derive_decision(role_items, kept, line.decision)


def submit(db: Session, caller: Caller, lines: Optional[List[dict]], background_tasks: Optional[BackgroundTasks],
           requester_note=_UNSET) -> dict:
    review, existing, active = _save_lines(db, caller, lines, requester_note)
    _normalise_for_submit(db, review, existing, active)

    plan = compute_plan(db, caller.user)
    now = utcnow()
    review.route = plan.route
    review.submitted_at = now
    review.comment = None
    if plan.route == ROUTE_BYPASS:
        review.status, review.decided_by, review.decided_at = APPROVED, caller.employee_id, now
    else:
        review.status, review.decided_by, review.decided_at = PENDING, None, None
    repo.replace_approvers(db, review.id, plan.approvers)
    db.commit()

    if plan.approvers:
        notify_approvers(db, review, background_tasks)
    return review_detail(db, caller, review.id)


# ---------------------------- use cases: approver ----------------------------

def _load_visible(db: Session, caller: Caller, review_id: int) -> Tuple[ArReview, List[str]]:
    review = repo.get_review_by_id(db, review_id)
    if review is None:
        raise ArNotFound(MSG_NOT_FOUND)
    approver_ids = [a.employee_id for a in repo.approver_rows(db, review.id)]
    if not can_view(review, approver_ids, caller):
        raise ArForbidden(MSG_FORBIDDEN_VIEW)
    return review, approver_ids


def review_detail(db: Session, caller: Caller, review_id: int) -> dict:
    review, approver_ids = _load_visible(db, caller, review_id)
    owner = repo.get_user(db, review.employee_id)
    lines = repo.review_lines(db, review.id)
    unchecked = repo.unchecked_by_line(db, [line.id for line in lines])
    rows = {ur.id: (ur, role, system) for ur, role, system in
            repo.assignments_by_ids(db, [line.user_role_id for line in lines])}
    counts = repo.item_counts(db, [role.id for _, role, _ in rows.values()])
    snapshots = repo.snapshot_by_line(db, [line.id for line in lines]) if review.status != DRAFT else {}
    out_lines = []
    for line in lines:
        row = rows.get(line.user_role_id)
        if row is None:
            continue
        user_role, role, system = row
        ids = unchecked.get(line.id, [])
        out_lines.append({
            "id": line.id, "user_role_id": line.user_role_id, "system_code": system.code,
            "system_name": system.name, "role_id": role.id, "role_name": role.name,
            "account_name": account_name(user_role, owner), "decision": line.decision or KEEP, "note": line.note,
            "item_count": len(snapshots[line.id]) if line.id in snapshots else counts.get(role.id, 0),
            "unchecked_item_ids": ids, "unchecked_count": len(ids),
        })
    out_lines.sort(key=lambda x: (x["system_code"], x["role_name"]))
    summary = _review_summary(db, review)
    approver_users = repo.users_by_employee_ids(db, approver_ids)
    return {
        "id": review.id,
        "status": review.status,
        "route": review.route,
        "cycle": {"id": review.cycle.id, "name": review.cycle.name} if review.cycle else None,
        "requester": person(owner, review.employee_id),
        "requester_note": review.requester_note,
        "lines": out_lines,
        "approvers": _approver_people(db, approver_ids, with_email=False),
        "next_approver_names": next_approver_names(review, approver_ids, approver_users),
        "can_decide": can_decide(review, approver_ids, caller),
        "submitted_at": summary["submitted_at"],
        "decided_by": summary["decided_by"],
        "decided_by_name": summary["decided_by_name"],
        "decided_at": summary["decided_at"],
        "comment": summary["comment"],
    }


def _review_rows(db: Session, reviews: List[ArReview]) -> List[dict]:
    ids = [r.id for r in reviews]
    approvers = repo.approvers_for_reviews(db, ids)
    users = repo.users_by_employee_ids(
        db, [r.employee_id for r in reviews] + [r.decided_by for r in reviews]
        + [e for eids in approvers.values() for e in eids])
    counts = repo.line_counts(db, ids)
    unchecked = repo.unchecked_counts(db, ids)
    out = []
    for r in reviews:
        total, removes = counts.get(r.id, (0, 0))
        decider = users.get(r.decided_by) if r.decided_by else None
        out.append({
            "id": r.id, "requester": person(users.get(r.employee_id), r.employee_id),
            "submitted_at": iso(r.submitted_at), "total_lines": total, "remove_count": removes, "route": r.route,
            # additive
            "status": r.status, "unchecked_count": unchecked.get(r.id, 0),
            "next_approver_names": next_approver_names(r, approvers.get(r.id, []), users),
            "decided_at": iso(r.decided_at),
            "decided_by_name": (full_name(decider) if decider else r.decided_by) if r.decided_by else None,
        })
    return out


def pending_list(db: Session, caller: Caller) -> List[dict]:
    return _review_rows(db, repo.pending_reviews_for(db, caller.employee_id, caller.is_admin))


def history_list(db: Session, caller: Caller) -> List[dict]:
    return _review_rows(db, repo.history_reviews_for(db, caller.employee_id))


def decide(db: Session, caller: Caller, review_id: int, action: str, comment: Optional[str]) -> dict:
    review, approver_ids = _load_visible(db, caller, review_id)
    is_decider = caller.employee_id in approver_ids or (review.route == ROUTE_IT_QUEUE and caller.is_admin)
    if not is_decider or caller.employee_id == review.employee_id:
        raise ArForbidden(MSG_FORBIDDEN_DECIDE)
    if review.status != PENDING:
        raise ArConflict(MSG_NOT_PENDING)
    text = (comment or "").strip() or None
    if action == "reject" and not text:
        raise ArError(MSG_REJECT_COMMENT)
    review.status = APPROVED if action == "approve" else REJECTED
    review.decided_by = caller.employee_id
    review.decided_at = utcnow()
    review.comment = text
    db.commit()
    return review_detail(db, caller, review.id)


def role_items_view(db: Session, role_id: int) -> dict:
    found = repo.get_role_with_system(db, role_id)
    if found is None:
        raise ArNotFound(MSG_ROLE_NOT_FOUND)
    role, system = found
    return {
        "role": {"id": role.id, "system_code": system.code, "name": role.name},
        "items": [{"id": i.id, "category": i.category, "item": i.item, "permission": i.permission}
                  for i in repo.role_items(db, role.id)],
    }


# ---------------------------- mail ----------------------------

def _mail_context(db: Session, review: ArReview) -> Tuple[List[str], dict]:
    approver_ids = [a.employee_id for a in repo.approver_rows(db, review.id)]
    users = repo.users_by_employee_ids(db, approver_ids + [review.employee_id])
    owner = users.get(review.employee_id)
    to = [users[e].email for e in approver_ids if e in users and users[e].email]
    total, removes = repo.line_counts(db, [review.id]).get(review.id, (0, 0))
    unchecked = repo.unchecked_counts(db, [review.id]).get(review.id, 0)
    p = person(owner, review.employee_id)
    cycle = review.cycle or repo.get_cycle(db, review.cycle_id)
    return to, {
        "review_id": review.id, "requester_name": p["name"], "requester_employee_id": review.employee_id,
        "department": p["department"], "division": p["division"], "cycle_name": cycle.name if cycle else None,
        "total_lines": total, "remove_count": removes, "unchecked_count": unchecked,
        "requester_note": review.requester_note,
    }


def mail_preview(db: Session, caller: Caller, review_id: int) -> dict:
    review, approver_ids = _load_visible(db, caller, review_id)
    if not (caller.is_admin or caller.employee_id in approver_ids):
        raise ArForbidden(MSG_FORBIDDEN_VIEW)
    to, ctx = _mail_context(db, review)
    subject, html = mail.render_approver_mail(ctx)
    return {"to": to, "subject": subject, "html": html}


def notify_approvers(db: Session, review: ArReview, background_tasks: Optional[BackgroundTasks]) -> None:
    to, ctx = _mail_context(db, review)
    subject, html = mail.render_approver_mail(ctx)
    mail.dispatch(to, subject, html, background_tasks)
    now = utcnow()
    for row in repo.approver_rows(db, review.id):
        row.mailed_at = now
    db.commit()
