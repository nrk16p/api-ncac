"""Access Review — SQLAlchemy queries (no business rules, no commits)."""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import func, inspect, or_
from sqlalchemy.orm import Session, joinedload

from models.access_review_model import (
    ArCycle, ArReview, ArReviewApprover, ArReviewLine, ArReviewLineItem, ArReviewLineSnapshot, ArRole, ArRoleItem,
    ArSystem, ArUserRole,
)
from models.form_approver_division import FormApproverDivision
from models.user_model import Department, Position, User

# (user_role, role, system)
Assignment = Tuple[ArUserRole, ArRole, ArSystem]


# ---------------------------- people ----------------------------

def _user_query(db: Session):
    return db.query(User).options(
        joinedload(User.department).joinedload(Department.division),
        joinedload(User.position).joinedload(Position.level),
    )


def get_user(db: Session, employee_id: Optional[str]) -> Optional[User]:
    if not employee_id:
        return None
    return _user_query(db).filter(User.employee_id == employee_id).first()


def users_by_employee_ids(db: Session, employee_ids: Iterable[Optional[str]]) -> Dict[str, User]:
    ids = {e for e in employee_ids if e}
    if not ids:
        return {}
    out: Dict[str, User] = {}
    for u in _user_query(db).filter(User.employee_id.in_(ids)).all():
        out.setdefault(u.employee_id, u)
    return out


def active_users_in_division(db: Session, division_id: Optional[int]) -> List[User]:
    if division_id is None:
        return []
    return (
        _user_query(db)
        .join(Department, Department.department_id == User.department_id)
        .filter(Department.division_id == division_id, User.employee_status == "Active")
        .all()
    )


def division_approver_ids(db: Session, division_id: Optional[int]) -> List[str]:
    """Active form_approver_divisions rows for the division whose user still exists and is Active."""
    if division_id is None:
        return []
    rows = (
        db.query(FormApproverDivision.employee_id)
        .join(User, User.employee_id == FormApproverDivision.employee_id)
        .filter(
            FormApproverDivision.division_id == division_id,
            FormApproverDivision.is_active.is_(True),
            User.employee_status == "Active",
        )
        .order_by(FormApproverDivision.id)
        .all()
    )
    return list(dict.fromkeys(r[0] for r in rows))


def all_users_for_matching(db: Session) -> List[Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]]:
    """(employee_id, username, email, employee_status) of every user — importer matching."""
    return db.query(User.employee_id, User.username, User.email, User.employee_status).all()


def employee_ids_matching(db: Session, q: str):
    """Subquery of employee_ids whose first/last name or employee_id contains q."""
    like = f"%{q}%"
    full = func.concat(func.coalesce(User.firstname, ""), " ", func.coalesce(User.lastname, ""))
    return db.query(User.employee_id).filter(
        or_(full.ilike(like), User.employee_id.ilike(like), User.username.ilike(like))
    )


# ---------------------------- cycles / reviews ----------------------------

def current_cycle(db: Session) -> Optional[ArCycle]:
    """Latest open cycle; when none is open, the latest cycle."""
    order = (ArCycle.start_date.desc(), ArCycle.id.desc())
    cycle = db.query(ArCycle).filter(ArCycle.is_open.is_(True)).order_by(*order).first()
    return cycle or db.query(ArCycle).order_by(*order).first()


def get_cycle(db: Session, cycle_id: int) -> Optional[ArCycle]:
    return db.get(ArCycle, cycle_id)


def list_cycles(db: Session) -> List[ArCycle]:
    return db.query(ArCycle).order_by(ArCycle.start_date.desc(), ArCycle.id.desc()).all()


def get_review(db: Session, cycle_id: int, employee_id: str) -> Optional[ArReview]:
    return db.query(ArReview).filter(ArReview.cycle_id == cycle_id, ArReview.employee_id == employee_id).first()


def get_review_by_id(db: Session, review_id: int) -> Optional[ArReview]:
    return db.query(ArReview).options(joinedload(ArReview.cycle)).filter(ArReview.id == review_id).first()


def reviews_for_cycle(db: Session, cycle_id: int) -> List[ArReview]:
    return db.query(ArReview).filter(ArReview.cycle_id == cycle_id).all()


def review_lines(db: Session, review_id: int) -> List[ArReviewLine]:
    return db.query(ArReviewLine).filter(ArReviewLine.review_id == review_id).order_by(ArReviewLine.id).all()


def approver_rows(db: Session, review_id: int) -> List[ArReviewApprover]:
    return db.query(ArReviewApprover).filter(ArReviewApprover.review_id == review_id).all()


def approvers_for_reviews(db: Session, review_ids: Iterable[int]) -> Dict[int, List[str]]:
    ids = list(set(review_ids))
    out: Dict[int, List[str]] = {i: [] for i in ids}
    if ids:
        for rid, eid in (db.query(ArReviewApprover.review_id, ArReviewApprover.employee_id)
                         .filter(ArReviewApprover.review_id.in_(ids)).all()):
            out[rid].append(eid)
    return out


def replace_approvers(db: Session, review_id: int, employee_ids: List[str]) -> None:
    db.query(ArReviewApprover).filter(ArReviewApprover.review_id == review_id).delete(synchronize_session=False)
    for eid in employee_ids:
        db.add(ArReviewApprover(review_id=review_id, employee_id=eid))


def history_reviews_for(db: Session, employee_id: str) -> List[ArReview]:
    """Finished reviews the caller owns or decided (newest decision first)."""
    return (
        db.query(ArReview)
        .filter(ArReview.status.in_(("approved", "rejected")),
                or_(ArReview.employee_id == employee_id, ArReview.decided_by == employee_id))
        .order_by(ArReview.decided_at.desc(), ArReview.id.desc())
        .all()
    )


def pending_reviews_for(db: Session, employee_id: str, include_it_queue: bool) -> List[ArReview]:
    as_approver = db.query(ArReviewApprover.review_id).filter(ArReviewApprover.employee_id == employee_id)
    cond = ArReview.id.in_(as_approver)
    if include_it_queue:
        cond = or_(cond, ArReview.route == "it_queue")
    return (
        db.query(ArReview)
        .filter(ArReview.status == "pending", ArReview.employee_id != employee_id, cond)
        .order_by(ArReview.submitted_at.asc(), ArReview.id.asc())
        .all()
    )


def line_counts(db: Session, review_ids: Iterable[int]) -> Dict[int, Tuple[int, int]]:
    """review_id → (total_lines, remove_count)."""
    ids = list(set(review_ids))
    if not ids:
        return {}
    rows = (
        db.query(
            ArReviewLine.review_id,
            func.count(ArReviewLine.id),
            func.count(ArReviewLine.id).filter(ArReviewLine.decision == "remove"),
        )
        .filter(ArReviewLine.review_id.in_(ids))
        .group_by(ArReviewLine.review_id)
        .all()
    )
    return {r[0]: (int(r[1] or 0), int(r[2] or 0)) for r in rows}


# ---------------------------- assignments ----------------------------

def _assignment_query(db: Session):
    return (
        db.query(ArUserRole, ArRole, ArSystem)
        .join(ArRole, ArRole.id == ArUserRole.role_id)
        .join(ArSystem, ArSystem.id == ArRole.system_id)
    )


def _active(q):
    return q.filter(ArUserRole.is_active.is_(True), ArRole.is_active.is_(True), ArSystem.is_active.is_(True))


def active_assignments(db: Session, employee_id: str) -> List[Assignment]:
    return (
        _active(_assignment_query(db))
        .filter(ArUserRole.employee_id == employee_id)
        .order_by(ArSystem.id, ArRole.name)
        .all()
    )


def assignments_by_ids(db: Session, user_role_ids: Iterable[int]) -> List[Assignment]:
    ids = list(set(user_role_ids))
    if not ids:
        return []
    return _assignment_query(db).filter(ArUserRole.id.in_(ids)).order_by(ArSystem.id, ArRole.name).all()


def employees_with_active_assignments(db: Session) -> List[str]:
    rows = _active(db.query(ArUserRole.employee_id)
                   .join(ArRole, ArRole.id == ArUserRole.role_id)
                   .join(ArSystem, ArSystem.id == ArRole.system_id)).distinct().all()
    return [r[0] for r in rows]


def item_counts(db: Session, role_ids: Iterable[int]) -> Dict[int, int]:
    ids = list(set(role_ids))
    if not ids:
        return {}
    rows = (
        db.query(ArRoleItem.role_id, func.count(ArRoleItem.id))
        .filter(ArRoleItem.role_id.in_(ids), ArRoleItem.is_active.is_(True))
        .group_by(ArRoleItem.role_id)
        .all()
    )
    return {r[0]: int(r[1]) for r in rows}


def active_user_counts(db: Session, role_ids: Iterable[int]) -> Dict[int, int]:
    ids = list(set(role_ids))
    if not ids:
        return {}
    rows = (
        db.query(ArUserRole.role_id, func.count(ArUserRole.id))
        .filter(ArUserRole.role_id.in_(ids), ArUserRole.is_active.is_(True))
        .group_by(ArUserRole.role_id)
        .all()
    )
    return {r[0]: int(r[1]) for r in rows}


def role_items(db: Session, role_id: int) -> List[ArRoleItem]:
    return (
        db.query(ArRoleItem)
        .filter(ArRoleItem.role_id == role_id, ArRoleItem.is_active.is_(True))
        .order_by(ArRoleItem.category, ArRoleItem.item, ArRoleItem.permission, ArRoleItem.id)
        .all()
    )


def get_role_with_system(db: Session, role_id: int) -> Optional[Tuple[ArRole, ArSystem]]:
    return (
        db.query(ArRole, ArSystem).join(ArSystem, ArSystem.id == ArRole.system_id).filter(ArRole.id == role_id).first()
    )


def get_system_by_code(db: Session, code: str) -> Optional[ArSystem]:
    return db.query(ArSystem).filter(ArSystem.code == code).first()


def ar_tables_exist(db: Session) -> bool:
    """False before the migration has run (lets an import dry run work against a DB without ar_* tables)."""
    return inspect(db.connection()).has_table("ar_systems")


# ---------------------------- item-level review ----------------------------

def items_for_roles(db: Session, role_ids: Iterable[int], include_ids: Iterable[int] = ()) -> Dict[int, List[ArRoleItem]]:
    """role_id → active items, plus any (inactive) item in include_ids — a review's unticked items stay visible."""
    ids = list(set(role_ids))
    if not ids:
        return {}
    extra = list(set(include_ids))
    cond = ArRoleItem.is_active.is_(True)
    if extra:
        cond = or_(cond, ArRoleItem.id.in_(extra))
    out: Dict[int, List[ArRoleItem]] = {i: [] for i in ids}
    for item in (db.query(ArRoleItem).filter(ArRoleItem.role_id.in_(ids), cond)
                 .order_by(ArRoleItem.role_id, ArRoleItem.category, ArRoleItem.item, ArRoleItem.permission,
                           ArRoleItem.id).all()):
        out[item.role_id].append(item)
    return out


def active_item_ids_by_role(db: Session, role_ids: Iterable[int]) -> Dict[int, set]:
    ids = list(set(role_ids))
    out: Dict[int, set] = {i: set() for i in ids}
    if ids:
        for rid, iid in (db.query(ArRoleItem.role_id, ArRoleItem.id)
                         .filter(ArRoleItem.role_id.in_(ids), ArRoleItem.is_active.is_(True)).all()):
            out[rid].add(iid)
    return out


def unchecked_by_line(db: Session, line_ids: Iterable[int]) -> Dict[int, List[int]]:
    ids = list(set(line_ids))
    out: Dict[int, List[int]] = {i: [] for i in ids}
    if ids:
        for line_id, item_id in (db.query(ArReviewLineItem.review_line_id, ArReviewLineItem.role_item_id)
                                 .filter(ArReviewLineItem.review_line_id.in_(ids))
                                 .order_by(ArReviewLineItem.role_item_id).all()):
            out[line_id].append(item_id)
    return out


def set_unchecked(db: Session, line_id: int, item_ids: Iterable[int]) -> None:
    db.query(ArReviewLineItem).filter(ArReviewLineItem.review_line_id == line_id).delete(synchronize_session=False)
    for iid in sorted(set(item_ids)):
        db.add(ArReviewLineItem(review_line_id=line_id, role_item_id=iid))


def delete_line(db: Session, line: ArReviewLine) -> None:
    db.query(ArReviewLineItem).filter(ArReviewLineItem.review_line_id == line.id).delete(synchronize_session=False)
    db.query(ArReviewLineSnapshot).filter(ArReviewLineSnapshot.review_line_id == line.id).delete(
        synchronize_session=False)
    db.delete(line)


def set_snapshot(db: Session, line_id: int, item_ids: Iterable[int]) -> None:
    db.query(ArReviewLineSnapshot).filter(ArReviewLineSnapshot.review_line_id == line_id).delete(
        synchronize_session=False)
    for iid in sorted(set(item_ids)):
        db.add(ArReviewLineSnapshot(review_line_id=line_id, role_item_id=iid))


def snapshot_by_line(db: Session, line_ids: Iterable[int]) -> Dict[int, List[int]]:
    """line_id → item ids the role had at submit. Lines submitted before snapshots existed are missing."""
    ids = list(set(line_ids))
    out: Dict[int, List[int]] = {}
    if ids:
        for line_id, item_id in (db.query(ArReviewLineSnapshot.review_line_id, ArReviewLineSnapshot.role_item_id)
                                 .filter(ArReviewLineSnapshot.review_line_id.in_(ids))
                                 .order_by(ArReviewLineSnapshot.role_item_id).all()):
            out.setdefault(line_id, []).append(item_id)
    return out


def items_by_ids(db: Session, item_ids: Iterable[int]) -> Dict[int, ArRoleItem]:
    ids = list(set(item_ids))
    return {i.id: i for i in db.query(ArRoleItem).filter(ArRoleItem.id.in_(ids)).all()} if ids else {}


def unchecked_counts(db: Session, review_ids: Iterable[int]) -> Dict[int, int]:
    """review_id → number of unticked items (on lines that are not whole-role removals)."""
    ids = list(set(review_ids))
    if not ids:
        return {}
    rows = (
        db.query(ArReviewLine.review_id, func.count(ArReviewLineItem.role_item_id))
        .join(ArReviewLineItem, ArReviewLineItem.review_line_id == ArReviewLine.id)
        .filter(ArReviewLine.review_id.in_(ids), or_(ArReviewLine.decision.is_(None), ArReviewLine.decision != "remove"))
        .group_by(ArReviewLine.review_id)
        .all()
    )
    return {r[0]: int(r[1]) for r in rows}


def referenced_item_ids(db: Session, item_ids: Iterable[int]) -> set:
    ids = list(set(item_ids))
    if not ids:
        return set()
    return ({r[0] for r in db.query(ArReviewLineItem.role_item_id)
             .filter(ArReviewLineItem.role_item_id.in_(ids)).distinct().all()}
            | {r[0] for r in db.query(ArReviewLineSnapshot.role_item_id)
               .filter(ArReviewLineSnapshot.role_item_id.in_(ids)).distinct().all()})
