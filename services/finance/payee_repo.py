"""Finance payee account master — persistence + serialization."""
from __future__ import annotations


from models.finance_model import FinPayeeAccount, FinPayeeAccountLog, FinPayeeAccountRequest
from services.finance.advance_repo import people_by_employee_id


def _iso(value):
    return value.isoformat() if value is not None else None


def people_for(db, rows, extra_ids=()):
    """One lookup for every employee id referenced by `rows` (+ extra ids)."""
    ids = set(extra_ids)
    for r in rows:
        for attr in ("employee_id", "updated_by", "reviewed_by", "action_by"):
            ids.add(getattr(r, attr, None))
    return people_by_employee_id(db, ids)


def serialize_account(row, people):
    person = people.get(row.employee_id) or {}
    updater = people.get(row.updated_by) or {}
    return {
        "id": row.id, "employee_id": row.employee_id, "employee_name": person.get("name"),
        "department": person.get("department"), "bank": row.bank, "account_no": row.account_no,
        "account_name": row.account_name, "status": row.status, "source_request_id": row.source_request_id,
        "created_by": row.created_by, "created_at": _iso(row.created_at),
        "updated_by": row.updated_by, "updated_by_name": updater.get("name"), "updated_at": _iso(row.updated_at),
    }


def serialize_request(row, people, current_account=None):
    person = people.get(row.employee_id) or {}
    reviewer = people.get(row.reviewed_by) or {}
    current = None
    if current_account is not None:
        current = {"account_no": current_account.account_no, "account_name": current_account.account_name,
                   "status": current_account.status}
    return {
        "id": row.id, "employee_id": row.employee_id, "employee_name": person.get("name"),
        "department": person.get("department"), "position": person.get("position"), "bank": row.bank,
        "account_no": row.account_no, "account_name": row.account_name, "remark": row.remark,
        "status": row.status, "review_remark": row.review_remark, "reviewed_by": row.reviewed_by,
        "reviewed_by_name": reviewer.get("name"), "reviewed_at": _iso(row.reviewed_at),
        "created_at": _iso(row.created_at), "current_account": current,
    }


def serialize_log(row, people):
    return {"action": row.action, "changes": row.changes, "remark": row.remark, "action_by": row.action_by,
            "action_by_name": (people.get(row.action_by) or {}).get("name"), "created_at": _iso(row.created_at)}


def get_master(db, employee_id, for_update=False):
    query = db.query(FinPayeeAccount).filter(FinPayeeAccount.employee_id == employee_id)
    if for_update:
        query = query.with_for_update()
    return query.first()


def masters_by_employee(db, employee_ids):
    ids = {e for e in employee_ids if e}
    if not ids:
        return {}
    return {m.employee_id: m for m in db.query(FinPayeeAccount).filter(FinPayeeAccount.employee_id.in_(ids)).all()}


def list_accounts(db, q=None, status=None):
    query = db.query(FinPayeeAccount)
    if status:
        query = query.filter(FinPayeeAccount.status == status)
    rows = query.all()
    people = people_for(db, rows)
    if q:
        needle = q.strip().lower()
        rows = [r for r in rows if needle in r.employee_id.lower()
                or needle in (people.get(r.employee_id, {}).get("name") or "").lower()]
    rows.sort(key=lambda r: ((people.get(r.employee_id, {}).get("name") or "~"), r.employee_id))
    return [serialize_account(r, people) for r in rows]


def list_requests(db, status="PENDING"):
    rows = (db.query(FinPayeeAccountRequest).filter(FinPayeeAccountRequest.status == status)
            .order_by(FinPayeeAccountRequest.created_at, FinPayeeAccountRequest.id).all())
    people = people_for(db, rows)
    masters = masters_by_employee(db, [r.employee_id for r in rows])
    return [serialize_request(r, people, masters.get(r.employee_id)) for r in rows]


def request_dict(db, row):
    people = people_for(db, [row])
    return serialize_request(row, people, get_master(db, row.employee_id))


def account_dict(db, row):
    return serialize_account(row, people_for(db, [row]))


def list_logs(db, employee_id):
    rows = (db.query(FinPayeeAccountLog).filter(FinPayeeAccountLog.employee_id == employee_id)
            .order_by(FinPayeeAccountLog.created_at.desc(), FinPayeeAccountLog.id.desc()).all())
    people = people_for(db, rows)
    return [serialize_log(r, people) for r in rows]
