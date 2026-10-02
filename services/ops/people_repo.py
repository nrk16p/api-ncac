"""Resolves OPS people from the Postgres `users` table (SQLAlchemy, read-only).

Deviation worth flagging to the Next.js side: models/user_model.py's `User` ORM
model has no `role` column, and routes/auth.py's build_user_response() does not
return one either — yet menaIT-v2's frontend already reads `user.role === 'a'` as
admin in several places (app/ops/team.ts, navbar, settings...). We could not find
where that value is actually populated from this repo (no local .env / DB access
to check the live `users` table either), so `get_user_role()` below *probes* for a
`role` column with a guarded raw-SQL SELECT the first time it's called, caches
whether the column exists for the life of the process, and returns None (never
raises) if it doesn't. If the column truly doesn't exist in production, ops
"manager" status falls back to OPS_TEAM-username-only — still correct for the 4
named usernames, just missing the role=='a' admin bypass until that's wired up.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from models.user_model import User

# tri-state: None = not probed yet, True/False = probed result, cached per-process
_role_column_available: Optional[bool] = None


def _role_column_is_available(db: Session) -> bool:
    global _role_column_available
    if _role_column_available is not None:
        return _role_column_available
    try:
        db.execute(text("SELECT role FROM users LIMIT 0"))
        _role_column_available = True
    except (ProgrammingError, OperationalError):
        db.rollback()
        _role_column_available = False
    return _role_column_available


def get_user_role(db: Session, user_id: int) -> Optional[str]:
    """Best-effort lookup of users.role — see module docstring. Never raises."""
    if not _role_column_is_available(db):
        return None
    try:
        row = db.execute(text("SELECT role FROM users WHERE id = :id"), {"id": user_id}).first()
        return row[0] if row else None
    except (ProgrammingError, OperationalError):
        db.rollback()
        return None


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
