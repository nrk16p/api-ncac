"""DB reads + JSON shaping for the Finance advance-cash module. Business rules live in advance_logic."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from models.finance_model import FinAccount, FinAdvance, FinAdvanceLog
from models.master_model import FormApprovalLog, FormMaster, FormQuestion, FormSubmission, FormSubmissionValue
from models.user_model import Department, Site, User
from services.finance import advance_logic as logic

BKK = ZoneInfo("Asia/Bangkok")
ADVANCE_FORM_TYPE = "Advance"


def today_bkk() -> date:
    return datetime.now(BKK).date()


def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)  # naive timestamps in ncacdb are UTC
    return value.isoformat()


def _num(value):
    return float(value) if value is not None else None


def _full_name(user):
    if user is None:
        return None
    return f"{user.firstname or ''} {user.lastname or ''}".strip() or None


def get_advance_submission(db, form_id):
    return (
        db.query(FormSubmission)
        .join(FormMaster, FormMaster.id == FormSubmission.form_master_id)
        .filter(FormMaster.form_type == ADVANCE_FORM_TYPE, FormSubmission.form_id == form_id)
        .first()
    )


def request_values_by_submission(db, submission_ids):
    if not submission_ids:
        return {}
    rows = (
        db.query(FormSubmissionValue, FormQuestion)
        .join(FormQuestion, FormQuestion.id == FormSubmissionValue.question_id)
        .filter(FormSubmissionValue.submission_id.in_(submission_ids))
        .all()
    )
    grouped = {}
    for value, question in rows:
        grouped.setdefault(value.submission_id, []).append({
            "name": question.question_name,
            "type": question.question_type,
            "sort_order": question.sort_order,
            "text": value.value_text,
            "number": value.value_number,
            "date": value.value_date,
        })
    return {sid: logic.pick_request_values(items) for sid, items in grouped.items()}


def people_by_employee_id(db, employee_ids):
    ids = {e for e in employee_ids if e}
    if not ids:
        return {}
    departments = {d.department_id: d.department_name_th for d in db.query(Department).all()}
    sites = {s.site_id: (s.site_name_th, s.site_code) for s in db.query(Site).all()}
    people = {}
    for user in db.query(User).filter(User.employee_id.in_(ids)).all():
        site_name, site_code = sites.get(user.site_id, (None, None))
        people[user.employee_id] = {
            "employee_id": user.employee_id,
            "name": _full_name(user),
            "department": departments.get(user.department_id),
            "site": site_name,
            "site_code": site_code,
        }
    return people


def account_names(db):
    return {a.acc_code: a.acc_name for a in db.query(FinAccount).all()}


def serialize_fin(adv, acc_names):
    if adv is None:
        return None
    return {
        "acc_code": adv.acc_code,
        "acc_name": acc_names.get(adv.acc_code),
        "voucher_no": adv.voucher_no,
        "voucher_date": _iso(adv.voucher_date),
        "payment_doc_no": adv.payment_doc_no,
        "purpose": adv.purpose,
        "amount_paid": _num(adv.amount_paid),
        "transfer_date": _iso(adv.transfer_date),
        "clear_due_date": _iso(adv.clear_due_date),
        "paid_by": adv.paid_by,
        "paid_at": _iso(adv.paid_at),
        "clear_date": _iso(adv.clear_date),
        "amount_actual": _num(adv.amount_actual),
        "clear_doc_no": adv.clear_doc_no,
        "settle_amount": _num(adv.settle_amount),
        "settle_date": _iso(adv.settle_date),
        "remark": adv.remark,
        "clear_submitted_at": _iso(adv.clear_submitted_at),
        "review_remark": adv.review_remark,
        "closed_by": adv.closed_by,
        "closed_at": _iso(adv.closed_at),
        "fin_status": adv.fin_status,
    }


def serialize_advance(sub, adv, request, people, acc_names, today):
    status, overdue = logic.derive_status(
        sub.status_approve,
        adv.fin_status if adv is not None else None,
        adv.clear_due_date if adv is not None else None,
        today,
    )
    request = request or {}
    requester = people.get(sub.created_by) or {
        "employee_id": sub.created_by, "name": None, "department": None, "site": None, "site_code": None,
    }
    return {
        "form_id": sub.form_id,
        "submission_id": sub.id,
        "created_at": _iso(sub.created_at),
        "status_approve": sub.status_approve,
        "status": status,
        "status_label": logic.STATUS_LABELS[status],
        "overdue": overdue,
        "requester": requester,
        "request": {
            "purpose": request.get("purpose"),
            "amount": _num(request.get("amount")),
            "use_date": _iso(request.get("use_date")),
        },
        "fin": serialize_fin(adv, acc_names),
    }


def list_advances(db, *, status=None, overdue=None, employee_id=None, acc_code=None, date_from=None, date_to=None):
    query = (
        db.query(FormSubmission, FinAdvance)
        .join(FormMaster, FormMaster.id == FormSubmission.form_master_id)
        .outerjoin(FinAdvance, FinAdvance.submission_id == FormSubmission.id)
        .filter(FormMaster.form_type == ADVANCE_FORM_TYPE)
    )
    if employee_id:
        query = query.filter(FormSubmission.created_by == employee_id)
    if acc_code:
        query = query.filter(FinAdvance.acc_code == acc_code)
    if date_from:
        query = query.filter(FormSubmission.created_at >= datetime.combine(date_from, time.min))
    if date_to:
        query = query.filter(FormSubmission.created_at < datetime.combine(date_to + timedelta(days=1), time.min))
    rows = query.order_by(FormSubmission.id.desc()).all()

    requests = request_values_by_submission(db, [sub.id for sub, _ in rows])
    people = people_by_employee_id(db, [sub.created_by for sub, _ in rows])
    acc_names = account_names(db)
    today = today_bkk()
    items = [serialize_advance(sub, adv, requests.get(sub.id), people, acc_names, today) for sub, adv in rows]
    if status:
        items = [item for item in items if item["status"] == status]
    if overdue is not None:
        items = [item for item in items if item["overdue"] == overdue]
    return items


def get_advance_detail(db, form_id):
    sub = get_advance_submission(db, form_id)
    if sub is None:
        return None
    adv = db.query(FinAdvance).filter(FinAdvance.submission_id == sub.id).first()
    item = serialize_advance(
        sub, adv,
        request_values_by_submission(db, [sub.id]).get(sub.id),
        people_by_employee_id(db, [sub.created_by]),
        account_names(db),
        today_bkk(),
    )
    approval_rows = (
        db.query(FormApprovalLog, User)
        .outerjoin(User, User.id == FormApprovalLog.action_by)
        .filter(FormApprovalLog.submission_id == sub.id)
        .order_by(FormApprovalLog.id.asc())
        .all()
    )
    item["approval_logs"] = [
        {"level_no": log.level_no, "action": log.action, "remark": log.remark,
         "action_at": _iso(log.action_at), "actor_name": _full_name(user)}
        for log, user in approval_rows
    ]
    fin_logs = []
    if adv is not None:
        fin_logs = [
            {"action": log.action, "changes": log.changes, "remark": log.remark,
             "action_by": log.action_by, "created_at": _iso(log.created_at)}
            for log in db.query(FinAdvanceLog).filter(FinAdvanceLog.advance_id == adv.id)
            .order_by(FinAdvanceLog.id.asc()).all()
        ]
    item["fin_logs"] = fin_logs
    return item


def outstanding_summary(db):
    items = [i for i in list_advances(db) if i["fin"] is not None and i["status"] != logic.CLOSED]
    by_employee, by_account = {}, {}
    for item in items:
        amount = item["fin"]["amount_paid"] or 0.0
        person = item["requester"]
        emp = by_employee.setdefault(person["employee_id"], {
            "employee_id": person["employee_id"], "name": person["name"], "department": person["department"],
            "count": 0, "amount_paid": 0.0, "overdue": 0,
        })
        emp["count"] += 1
        emp["amount_paid"] = round(emp["amount_paid"] + amount, 2)
        emp["overdue"] += 1 if item["overdue"] else 0
        acc = by_account.setdefault(item["fin"]["acc_code"], {
            "acc_code": item["fin"]["acc_code"], "acc_name": item["fin"]["acc_name"], "count": 0, "amount_paid": 0.0,
        })
        acc["count"] += 1
        acc["amount_paid"] = round(acc["amount_paid"] + amount, 2)
    return {
        "total_count": len(items),
        "total_amount": round(sum((i["fin"]["amount_paid"] or 0.0) for i in items), 2),
        "by_employee": sorted(by_employee.values(), key=lambda e: -e["amount_paid"]),
        "by_account": sorted(by_account.values(), key=lambda a: -a["amount_paid"]),
    }
