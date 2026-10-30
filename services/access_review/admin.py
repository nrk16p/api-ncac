"""Access Review — IT-admin master data (systems, roles, items, user roles, cycles) and reports.

Roles / user_roles are soft-deleted (is_active=false); role items are hard-deleted.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.access_review_model import (
    ArCycle, ArReview, ArReviewLine, ArRole, ArRoleItem, ArSystem, ArUserRole,
)
from services.access_review import repo
from services.access_review.logic import (
    APPROVED, DRAFT, MSG_NOT_FOUND, PENDING, REMOVE, ArConflict, ArError, ArNotFound, account_name, cycle_dict,
    full_name, iso, item_dict, item_sort_key, next_approver_names, person, user_division_id,
)

MSG_SYSTEM_NOT_FOUND = "ไม่พบระบบ"
MSG_SYSTEM_EXISTS = "มีรหัสระบบนี้อยู่แล้ว"
MSG_ROLE_NOT_FOUND = "ไม่พบกลุ่มสิทธิ์"
MSG_ROLE_EXISTS = "มีกลุ่มสิทธิ์ชื่อนี้ในระบบนี้อยู่แล้ว"
MSG_ITEM_NOT_FOUND = "ไม่พบรายการสิทธิ์"
MSG_COPY_SAME_ROLE = "เลือกกลุ่มสิทธิ์ต้นทางเป็นกลุ่มเดียวกันไม่ได้"
MSG_COPY_OTHER_SYSTEM = "คัดลอกได้เฉพาะกลุ่มสิทธิ์ในระบบเดียวกัน"
MSG_EMPLOYEE_NOT_FOUND = "ไม่พบรหัสพนักงานในระบบ"
MSG_USER_ROLE_NOT_FOUND = "ไม่พบสิทธิ์ของผู้ใช้รายการนี้"
MSG_USER_ROLE_EXISTS = "พนักงานคนนี้มีกลุ่มสิทธิ์นี้อยู่แล้ว"
MSG_CYCLE_NOT_FOUND = "ไม่พบรอบการทบทวน"
MSG_CYCLE_DATES = "วันสิ้นสุดต้องไม่ก่อนวันเริ่มต้น"
MSG_CYCLE_HAS_REVIEWS = "รอบนี้มีการทบทวนสิทธิ์แล้ว {n} รายการ ลบไม่ได้ — ให้ปิดรอบแทน"


def _get(db: Session, model, obj_id: int, message: str):
    obj = db.get(model, obj_id)
    if obj is None:
        raise ArNotFound(message)
    return obj


def _commit_unique(db: Session, message: str) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ArConflict(message) from exc


# ---------------------------- systems ----------------------------

def system_dict(s: ArSystem) -> dict:
    return {"id": s.id, "code": s.code, "name": s.name, "is_active": bool(s.is_active)}


def list_systems(db: Session) -> List[dict]:
    return [system_dict(s) for s in db.query(ArSystem).order_by(ArSystem.id).all()]


def create_system(db: Session, data: dict) -> dict:
    if repo.get_system_by_code(db, data["code"]):
        raise ArConflict(MSG_SYSTEM_EXISTS)
    s = ArSystem(code=data["code"], name=data["name"], is_active=data.get("is_active", True))
    db.add(s)
    _commit_unique(db, MSG_SYSTEM_EXISTS)
    return system_dict(s)


def update_system(db: Session, system_id: int, changes: dict) -> dict:
    s = _get(db, ArSystem, system_id, MSG_SYSTEM_NOT_FOUND)
    for key in ("code", "name", "is_active"):
        if key in changes and changes[key] is not None:
            setattr(s, key, changes[key])
    _commit_unique(db, MSG_SYSTEM_EXISTS)
    return system_dict(s)


# ---------------------------- roles ----------------------------

def _role_dicts(db: Session, rows: List[tuple]) -> List[dict]:
    ids = [r.id for r, _ in rows]
    items = repo.item_counts(db, ids)
    users = repo.active_user_counts(db, ids)
    return [{"id": r.id, "system_id": s.id, "system_code": s.code, "system_name": s.name, "name": r.name,
             "is_active": bool(r.is_active), "item_count": items.get(r.id, 0), "user_count": users.get(r.id, 0)}
            for r, s in rows]


def list_roles(db: Session, system_id: Optional[int], q: Optional[str], include_inactive: bool) -> List[dict]:
    query = db.query(ArRole, ArSystem).join(ArSystem, ArSystem.id == ArRole.system_id)
    if system_id is not None:
        query = query.filter(ArRole.system_id == system_id)
    if q:
        query = query.filter(ArRole.name.ilike(f"%{q.strip()}%"))
    if not include_inactive:
        query = query.filter(ArRole.is_active.is_(True))
    return _role_dicts(db, query.order_by(ArSystem.id, ArRole.name).all())


def _role_out(db: Session, role: ArRole) -> dict:
    return _role_dicts(db, [(role, db.get(ArSystem, role.system_id))])[0]


def create_role(db: Session, data: dict) -> dict:
    _get(db, ArSystem, data["system_id"], MSG_SYSTEM_NOT_FOUND)
    role = db.query(ArRole).filter(ArRole.system_id == data["system_id"], ArRole.name == data["name"]).first()
    if role is not None and role.is_active:
        raise ArConflict(MSG_ROLE_EXISTS)
    if role is None:
        role = ArRole(system_id=data["system_id"], name=data["name"], is_active=True)
        db.add(role)
    else:
        role.is_active = True  # re-adding a soft-deleted role restores it
    _commit_unique(db, MSG_ROLE_EXISTS)
    return _role_out(db, role)


def update_role(db: Session, role_id: int, changes: dict) -> dict:
    role = _get(db, ArRole, role_id, MSG_ROLE_NOT_FOUND)
    for key in ("name", "is_active"):
        if key in changes and changes[key] is not None:
            setattr(role, key, changes[key])
    _commit_unique(db, MSG_ROLE_EXISTS)
    return _role_out(db, role)


def delete_role(db: Session, role_id: int) -> dict:
    role = _get(db, ArRole, role_id, MSG_ROLE_NOT_FOUND)
    role.is_active = False
    db.commit()
    return _role_out(db, role)


# ---------------------------- role items ----------------------------

def item_dict(i: ArRoleItem) -> dict:
    return {"id": i.id, "role_id": i.role_id, "category": i.category, "item": i.item, "permission": i.permission}


def list_items(db: Session, role_id: int) -> List[dict]:
    _get(db, ArRole, role_id, MSG_ROLE_NOT_FOUND)
    return [item_dict(i) for i in repo.role_items(db, role_id)]


def create_item(db: Session, role_id: int, data: dict) -> dict:
    _get(db, ArRole, role_id, MSG_ROLE_NOT_FOUND)
    item = ArRoleItem(role_id=role_id, category=data["category"], item=data.get("item"),
                      permission=data.get("permission"))
    db.add(item)
    db.commit()
    return item_dict(item)


def copy_items(db: Session, role_id: int, source_role_id: int) -> dict:
    """Copies the source role's active items into role_id in one commit; items already there are skipped."""
    role = _get(db, ArRole, role_id, MSG_ROLE_NOT_FOUND)
    source = _get(db, ArRole, source_role_id, MSG_ROLE_NOT_FOUND)
    if source.id == role.id:
        raise ArError(MSG_COPY_SAME_ROLE)
    if source.system_id != role.system_id:
        raise ArError(MSG_COPY_OTHER_SYSTEM)
    have = {(i.category, i.item, i.permission) for i in repo.role_items(db, role.id)}
    added = 0
    for i in repo.role_items(db, source.id):
        key = (i.category, i.item, i.permission)
        if key in have:
            continue
        have.add(key)
        db.add(ArRoleItem(role_id=role.id, category=i.category, item=i.item, permission=i.permission))
        added += 1
    db.commit()
    return {"added": added, "role": _role_out(db, role)}


def update_item(db: Session, item_id: int, changes: dict) -> dict:
    item = _get(db, ArRoleItem, item_id, MSG_ITEM_NOT_FOUND)
    for key in ("category", "item", "permission"):
        if key in changes:
            if key == "category" and not changes[key]:
                continue
            setattr(item, key, changes[key])
    db.commit()
    return item_dict(item)


def delete_item(db: Session, item_id: int) -> None:
    """Hard delete, unless a review references the item (then hidden with is_active=false, history kept)."""
    item = _get(db, ArRoleItem, item_id, MSG_ITEM_NOT_FOUND)
    if repo.referenced_item_ids(db, [item.id]):
        item.is_active = False
    else:
        db.delete(item)
    db.commit()


# ---------------------------- user roles ----------------------------

def _user_role_dicts(db: Session, rows: List[tuple]) -> List[dict]:
    """rows: (ArUserRole, ArRole, ArSystem)."""
    users = repo.users_by_employee_ids(db, [ur.employee_id for ur, _, _ in rows])
    out = []
    for ur, role, system in rows:
        u = users.get(ur.employee_id)
        p = person(u, ur.employee_id)
        out.append({
            "id": ur.id, "employee_id": ur.employee_id, "employee_name": p["name"], "department": p["department"],
            "division": p["division"], "email": u.email if u is not None else None, "system_id": system.id,
            "system_code": system.code, "role_id": role.id, "role_name": role.name,
            "account_name": ur.account_name, "is_active": bool(ur.is_active), "updated_at": iso(ur.updated_at),
            # additive
            "role_is_active": bool(role.is_active),
            "position": p["position"],
        })
    return out


def _user_role_query(db: Session):
    return (db.query(ArUserRole, ArRole, ArSystem)
            .join(ArRole, ArRole.id == ArUserRole.role_id)
            .join(ArSystem, ArSystem.id == ArRole.system_id))


def list_user_roles(db: Session, system_id: Optional[int], role_id: Optional[int], q: Optional[str],
                    include_inactive: bool, page: int, page_size: int) -> dict:
    query = _user_role_query(db)
    if system_id is not None:
        query = query.filter(ArRole.system_id == system_id)
    if role_id is not None:
        query = query.filter(ArUserRole.role_id == role_id)
    if not include_inactive:
        query = query.filter(ArUserRole.is_active.is_(True))
    if q and q.strip():
        term = q.strip()
        like = f"%{term}%"
        query = query.filter(or_(
            ArUserRole.employee_id.ilike(like),
            ArUserRole.account_name.ilike(like),
            ArUserRole.employee_id.in_(repo.employee_ids_matching(db, term)),
        ))
    total = query.order_by(None).count()
    rows = (query.order_by(ArSystem.id, ArUserRole.employee_id, ArRole.name)
            .offset((page - 1) * page_size).limit(page_size).all())
    return {"items": _user_role_dicts(db, rows), "total": total, "page": page, "page_size": page_size}


def _user_role_out(db: Session, ur_id: int) -> dict:
    return _user_role_dicts(db, [_user_role_query(db).filter(ArUserRole.id == ur_id).one()])[0]


def create_user_role(db: Session, data: dict) -> dict:
    if repo.get_user(db, data["employee_id"]) is None:
        raise ArError(MSG_EMPLOYEE_NOT_FOUND)
    if db.get(ArRole, data["role_id"]) is None:
        raise ArError(MSG_ROLE_NOT_FOUND)
    ur = (db.query(ArUserRole)
          .filter(ArUserRole.employee_id == data["employee_id"], ArUserRole.role_id == data["role_id"]).first())
    if ur is not None and ur.is_active:
        raise ArConflict(MSG_USER_ROLE_EXISTS)
    if ur is None:
        ur = ArUserRole(employee_id=data["employee_id"], role_id=data["role_id"],
                        account_name=data.get("account_name"), is_active=True)
        db.add(ur)
    else:
        ur.is_active = True  # re-adding a soft-deleted row restores it
        if data.get("account_name"):
            ur.account_name = data["account_name"]
    _commit_unique(db, MSG_USER_ROLE_EXISTS)
    return _user_role_out(db, ur.id)


def update_user_role(db: Session, ur_id: int, changes: dict) -> dict:
    ur = _get(db, ArUserRole, ur_id, MSG_USER_ROLE_NOT_FOUND)
    if "account_name" in changes:
        ur.account_name = changes["account_name"] or None
    if changes.get("is_active") is not None:
        ur.is_active = changes["is_active"]
    db.commit()
    return _user_role_out(db, ur.id)


def delete_user_role(db: Session, ur_id: int) -> dict:
    ur = _get(db, ArUserRole, ur_id, MSG_USER_ROLE_NOT_FOUND)
    ur.is_active = False
    db.commit()
    return _user_role_out(db, ur.id)


# ---------------------------- cycles ----------------------------

def list_cycles(db: Session) -> List[dict]:
    return [cycle_dict(c) for c in repo.list_cycles(db)]


def create_cycle(db: Session, data: dict) -> dict:
    if data["end_date"] < data["start_date"]:
        raise ArError(MSG_CYCLE_DATES)
    c = ArCycle(name=data["name"], start_date=data["start_date"], end_date=data["end_date"],
                is_open=data.get("is_open", True))
    db.add(c)
    db.commit()
    return cycle_dict(c)


def update_cycle(db: Session, cycle_id: int, changes: dict) -> dict:
    c = _get(db, ArCycle, cycle_id, MSG_CYCLE_NOT_FOUND)
    for key in ("name", "start_date", "end_date", "is_open"):
        if key in changes and changes[key] is not None:
            setattr(c, key, changes[key])
    if c.end_date < c.start_date:
        db.rollback()
        raise ArError(MSG_CYCLE_DATES)
    db.commit()
    return cycle_dict(c)


def delete_cycle(db: Session, cycle_id: int) -> dict:
    """Hard delete, only while no one has reviewed in the cycle — reviews are the audit trail, so close it instead."""
    c = _get(db, ArCycle, cycle_id, MSG_CYCLE_NOT_FOUND)
    n = db.query(ArReview).filter(ArReview.cycle_id == cycle_id).count()
    if n:
        raise ArConflict(MSG_CYCLE_HAS_REVIEWS.format(n=n))
    out = cycle_dict(c)
    db.delete(c)
    db.commit()
    return out


# ---------------------------- reports ----------------------------

def _resolve_cycle(db: Session, cycle_id: Optional[int]) -> Optional[ArCycle]:
    if cycle_id is None:
        return repo.current_cycle(db)
    return _get(db, ArCycle, cycle_id, MSG_CYCLE_NOT_FOUND)


def _decider_name(review: Optional[ArReview], users: Dict) -> Optional[str]:
    if review is None or not review.decided_by:
        return None
    return full_name(users.get(review.decided_by)) or review.decided_by


def reviews_report(db: Session, cycle_id: Optional[int], status: Optional[str], division_id: Optional[int]) -> dict:
    cycle = _resolve_cycle(db, cycle_id)
    reviews = {r.employee_id: r for r in repo.reviews_for_cycle(db, cycle.id)} if cycle else {}
    people = list(dict.fromkeys(repo.employees_with_active_assignments(db) + list(reviews)))
    approvers = repo.approvers_for_reviews(db, [r.id for r in reviews.values()])
    users = repo.users_by_employee_ids(
        db, people + [r.decided_by for r in reviews.values() if r.decided_by]
        + [e for eids in approvers.values() for e in eids])
    counts = repo.line_counts(db, [r.id for r in reviews.values()])
    unchecked = repo.unchecked_counts(db, [r.id for r in reviews.values()])

    summary = {"total_people": 0, "not_started": 0, "draft": 0, "pending": 0, "approved": 0, "rejected": 0}
    items = []
    for eid in people:
        u = users.get(eid)
        if division_id is not None and (u is None or user_division_id(u) != division_id):
            continue
        r = reviews.get(eid)
        st = r.status if r is not None else "not_started"
        summary["total_people"] += 1
        summary[st] = summary.get(st, 0) + 1
        if status and st != status:
            continue
        total, removes = counts.get(r.id, (0, 0)) if r is not None else (0, 0)
        p = person(u, eid)
        items.append({
            "employee_id": eid, "name": p["name"], "department": p["department"], "division": p["division"],
            "status": st, "route": r.route if r else None, "submitted_at": iso(r.submitted_at) if r else None,
            "decided_at": iso(r.decided_at) if r else None, "remove_count": removes, "total_lines": total,
            "review_id": r.id if r else None,
            # additive
            "unchecked_count": unchecked.get(r.id, 0) if r else 0,
            "next_approver_names": next_approver_names(r, approvers.get(r.id, []), users) if r else [],
            "decided_by_name": _decider_name(r, users),
        })
    items.sort(key=lambda x: (x["division"] or "", x["department"] or "", x["name"] or ""))
    return {"cycle": cycle_dict(cycle), "summary": summary, "items": items}


def removals_report(db: Session, cycle_id: Optional[int], include_pending: bool = False) -> List[dict]:
    """IT to-do list of an approved cycle: one row per whole-role removal (category/item/permission null) and one
    row per unticked item of a kept role. include_pending adds reviews still waiting for approval."""
    cycle = _resolve_cycle(db, cycle_id)
    if cycle is None:
        return []
    rows = (
        db.query(ArReview, ArReviewLine, ArUserRole, ArRole, ArSystem)
        .join(ArReviewLine, ArReviewLine.review_id == ArReview.id)
        .join(ArUserRole, ArUserRole.id == ArReviewLine.user_role_id)
        .join(ArRole, ArRole.id == ArUserRole.role_id)
        .join(ArSystem, ArSystem.id == ArRole.system_id)
        .filter(ArReview.cycle_id == cycle.id,
                ArReview.status.in_((APPROVED, PENDING) if include_pending else (APPROVED,)))
        .order_by(ArReview.decided_at, ArReview.employee_id, ArSystem.id, ArRole.name)
        .all()
    )
    unchecked = repo.unchecked_by_line(db, [line.id for _, line, *_ in rows if line.decision != REMOVE])
    item_ids = {i for ids in unchecked.values() for i in ids}
    items = {i.id: i for i in db.query(ArRoleItem).filter(ArRoleItem.id.in_(item_ids)).all()} if item_ids else {}
    users = repo.users_by_employee_ids(db, [r.employee_id for r, *_ in rows] + [r.decided_by for r, *_ in rows])
    out = []
    for review, line, user_role, role, system in rows:
        if line.decision == REMOVE:
            targets = [None]
        else:
            targets = [items[i] for i in sorted(unchecked.get(line.id, []), key=lambda i: (
                items[i].category, items[i].item or "", items[i].permission or "")) if i in items]
        if not targets:
            continue
        owner = users.get(review.employee_id)
        p = person(owner, review.employee_id)
        decider = users.get(review.decided_by)
        base = {
            "employee_id": review.employee_id, "name": p["name"], "department": p["department"],
            "system_code": system.code, "role_name": role.name,
            "account_name": user_role.account_name or (owner.email if owner is not None else None),
            "note": line.note, "requester_note": review.requester_note, "decided_at": iso(review.decided_at),
            "decided_by_name": full_name(decider) if decider else review.decided_by,
            # additive
            "review_id": review.id, "user_role_id": line.user_role_id, "user_role_active": bool(user_role.is_active),
            "status": review.status,
        }
        for it in targets:
            out.append({**base,
                        "removal_type": "role" if it is None else "item",
                        "role_item_id": it.id if it is not None else None,
                        "category": it.category if it is not None else None,
                        "item": it.item if it is not None else None,
                        "permission": it.permission if it is not None else None})
    return out


def review_changes(db: Session, review_id: int) -> dict:
    """Before → after of one review, per role: "before" = the items the role had at submit (snapshot), falling back
    to the live items for drafts and for reviews submitted before snapshots existed; "after" = before − unticked.
    added_since / retired_since show how the role changed in the source system after submit."""
    review = _get(db, ArReview, review_id, MSG_NOT_FOUND)
    lines = repo.review_lines(db, review.id)
    rows = {ur.id: (ur, role, system) for ur, role, system in
            repo.assignments_by_ids(db, [line.user_role_id for line in lines])}
    unchecked = repo.unchecked_by_line(db, [line.id for line in lines])
    snapshots = repo.snapshot_by_line(db, [line.id for line in lines]) if review.status != DRAFT else {}
    live = repo.items_for_roles(db, [role.id for _, role, _ in rows.values()])
    items = repo.items_by_ids(db, [i for ids in list(snapshots.values()) + list(unchecked.values()) for i in ids])
    for role_items in live.values():
        items.update({i.id: i for i in role_items})
    users = repo.users_by_employee_ids(db, [review.employee_id, review.decided_by])
    owner = users.get(review.employee_id)

    def dicts(ids):
        return [item_dict(i) for i in sorted((items[i] for i in ids if i in items), key=item_sort_key)]

    out_lines = []
    for line in lines:
        row = rows.get(line.user_role_id)
        if row is None:
            continue
        user_role, role, system = row
        live_ids = [i.id for i in live.get(role.id, [])]
        removed = unchecked.get(line.id, [])
        has_snapshot = line.id in snapshots
        before = snapshots[line.id] if has_snapshot else sorted(set(live_ids) | set(removed))
        whole_role = line.decision == REMOVE
        out_lines.append({
            "user_role_id": line.user_role_id, "system_code": system.code, "system_name": system.name,
            "role_id": role.id, "role_name": role.name, "account_name": account_name(user_role, owner),
            "decision": line.decision or "keep",
            "change": "role" if whole_role else ("items" if removed else None),
            "has_snapshot": has_snapshot or not before,
            "before_count": len(before),
            "after_count": 0 if whole_role else len(before) - len(removed),
            "removed": dicts(removed),
            "added_since": dicts([i for i in live_ids if i not in set(before)]) if has_snapshot else [],
            "retired_since": dicts([i for i in before if i in items and not items[i].is_active]),
            "note": line.note,
            "user_role_active": bool(user_role.is_active),
        })
    out_lines.sort(key=lambda x: (x["system_code"], x["role_name"]))
    decider = users.get(review.decided_by) if review.decided_by else None
    return {
        "review_id": review.id, "status": review.status, "requester": person(owner, review.employee_id),
        "submitted_at": iso(review.submitted_at), "decided_at": iso(review.decided_at),
        "decided_by_name": (full_name(decider) or review.decided_by) if review.decided_by else None,
        "requester_note": review.requester_note, "comment": review.comment,
        "has_snapshot": all(line["has_snapshot"] for line in out_lines),
        "lines": out_lines,
    }
