"""Resolves OPS people from the Postgres `users` table (SQLAlchemy, read-only)."""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from models.user_model import User


def person_from_user(user: User) -> dict:
    name = " ".join(p for p in (user.firstname, user.lastname) if p).strip() or user.username
    department = user.department.department_name_en if user.department else None
    position = user.position.position_name_en if user.position else None
    return {
        "employee_id": user.employee_id or str(user.id),
        "name": name,
        "username": user.username,
        "image_url": user.image_url,
        "department": department,
        "position": position,
    }


def get_user_by_employee_id(db: Session, employee_id: str) -> Optional[User]:
    if not employee_id:
        return None
    return db.query(User).filter(User.employee_id == employee_id).first()


def get_users_by_usernames(db: Session, usernames: Iterable[str]) -> Dict[str, User]:
    """Case-insensitive lookup — key of the returned dict is the lowercased username."""
    wanted = {u.strip().lower() for u in usernames if u and u.strip()}
    if not wanted:
        return {}
    users = db.query(User).filter(User.username.isnot(None)).all()
    return {u.username.lower(): u for u in users if u.username and u.username.lower() in wanted}


def resolve_people(db: Session, usernames: Iterable[str]) -> List[dict]:
    """usernames (already validated/deduped by ops_logic.validate_assignee_usernames) → Person
    snapshots, in the same order. A username with no matching user is silently skipped
    (should not happen since OPS_TEAM usernames are expected to exist, but read-paths must
    not 500 over a stale roster)."""
    by_username = get_users_by_usernames(db, usernames)
    people = []
    for u in usernames:
        user = by_username.get(u.strip().lower())
        if user is not None:
            people.append(person_from_user(user))
    return people
