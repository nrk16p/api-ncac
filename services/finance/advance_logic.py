"""Pure business rules for the Finance advance-cash flow (no DB, no FastAPI).

The display status is derived from the approval state of the form submission plus the
fin_advances row. See menait-service docs/superpowers/specs/2026-09-28-finance-advance-design.md §6.1.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Iterable, Mapping

CLEAR_DUE_DAYS = 7
USE_DATE_MESSAGE = "วันที่ใช้เงินต้องเป็นวันนี้หรือหลังจากนี้"

PENDING_APPROVAL = "PENDING_APPROVAL"
REJECTED = "REJECTED"
AWAITING_VOUCHER = "AWAITING_VOUCHER"
AWAITING_PAYMENT = "AWAITING_PAYMENT"
AWAITING_CLEARING = "AWAITING_CLEARING"
SENT_BACK = "SENT_BACK"
AWAITING_REVIEW = "AWAITING_REVIEW"
CLOSED = "CLOSED"

STATUS_LABELS = {
    PENDING_APPROVAL: "รออนุมัติ",
    REJECTED: "ไม่อนุมัติ",
    AWAITING_VOUCHER: "รอตั้งเบิกทำจ่าย",
    AWAITING_PAYMENT: "รอจ่าย",
    AWAITING_CLEARING: "จ่ายแล้วรอเคลียร์",
    SENT_BACK: "ส่งกลับแก้ไข",
    AWAITING_REVIEW: "รอบัญชีตรวจ",
    CLOSED: "ปิดแล้ว",
}

FIN_VOUCHERED = "VOUCHERED"
FIN_PAID = "PAID"
FIN_CLEARING_SUBMITTED = "CLEARING_SUBMITTED"
FIN_SENT_BACK = "SENT_BACK"
FIN_CLOSED = "CLOSED"

_FIN_TO_STATUS = {
    FIN_VOUCHERED: AWAITING_PAYMENT,
    FIN_PAID: AWAITING_CLEARING,
    FIN_SENT_BACK: SENT_BACK,
    FIN_CLEARING_SUBMITTED: AWAITING_REVIEW,
    FIN_CLOSED: CLOSED,
}

_CENT = Decimal("0.01")


class AdvanceRuleError(Exception):
    """Validation failure → HTTP 400."""
    http_status = 400


class InvalidTransition(AdvanceRuleError):
    """Action not allowed in the current status → HTTP 409."""
    http_status = 409


class NotAllowed(AdvanceRuleError):
    """Actor may not perform this action → HTTP 403."""
    http_status = 403


def derive_status(status_approve, fin_status, clear_due_date, today):
    if fin_status:
        status = _FIN_TO_STATUS[fin_status]
    elif status_approve == "Approved":
        status = AWAITING_VOUCHER
    elif status_approve == "Rejected":
        status = REJECTED
    else:
        status = PENDING_APPROVAL
    overdue = (
        status in (AWAITING_CLEARING, SENT_BACK)
        and clear_due_date is not None
        and today > clear_due_date
    )
    return status, overdue


def default_due_date(transfer_date: date) -> date:
    return transfer_date + timedelta(days=CLEAR_DUE_DAYS)


def compute_settle_amount(amount_paid, amount_actual) -> Decimal:
    return (Decimal(amount_paid) - Decimal(amount_actual)).quantize(_CENT)


def _require(condition, message):
    if not condition:
        raise AdvanceRuleError(message)


def _require_status(current, allowed, action):
    if current not in allowed:
        raise InvalidTransition(
            f"ทำรายการ {action} ไม่ได้ในสถานะ {STATUS_LABELS.get(current, current)}"
        )


def check_pay(status, *, acc_active, amount_paid, transfer_date, clear_due_date, is_edit: bool = False):
    _require_status(status, (AWAITING_PAYMENT, AWAITING_CLEARING), "บันทึกการจ่ายเงิน")
    if status == AWAITING_PAYMENT and is_edit:
        raise InvalidTransition("ยังไม่มีข้อมูลการจ่ายให้แก้ไข กรุณารีเฟรชหน้าจอ")
    if status == AWAITING_CLEARING and not is_edit:
        raise InvalidTransition("รายการนี้ถูกบันทึกการจ่ายไปแล้ว กรุณารีเฟรชหน้าจอ")
    _require(acc_active, "กรุณาเลือกรหัสบัญชีที่ใช้งานอยู่")
    _require(amount_paid is not None and Decimal(amount_paid) >= 0, "ยอดเงินต้องไม่ติดลบ")
    _require(transfer_date is not None, "กรุณาระบุวันที่โอนเงิน")
    due = clear_due_date or default_due_date(transfer_date)
    _require(due >= transfer_date, "กำหนดการเคลียร์ต้องไม่ก่อนวันที่โอนเงิน")
    return due


def check_voucher(status, *, voucher_date, is_edit: bool = False):
    _require_status(status, (AWAITING_VOUCHER, AWAITING_PAYMENT, AWAITING_CLEARING), "บันทึกการตั้งเบิก")
    if status == AWAITING_VOUCHER and is_edit:
        raise InvalidTransition("ยังไม่มีข้อมูลการตั้งเบิกให้แก้ไข กรุณารีเฟรชหน้าจอ")
    if status != AWAITING_VOUCHER and not is_edit:
        raise InvalidTransition("รายการนี้ถูกตั้งเบิกไปแล้ว กรุณารีเฟรชหน้าจอ")
    _require(voucher_date is not None, "กรุณาระบุวันที่ตั้งเบิก")


def check_clear(status, *, is_owner, amount_paid, clear_date, amount_actual, settle_date):
    if not is_owner:
        raise NotAllowed("เฉพาะผู้เบิกเงินเท่านั้นที่บันทึกการเคลียร์ได้")
    _require_status(status, (AWAITING_CLEARING, SENT_BACK, AWAITING_REVIEW), "เคลียร์เงิน")
    _require(clear_date is not None, "กรุณาระบุวันที่ส่งเอกสารเคลียร์")
    _require(amount_actual is not None and Decimal(amount_actual) >= 0, "ยอดใช้จริงต้องไม่ติดลบ")
    settle = compute_settle_amount(amount_paid, amount_actual)
    if settle > 0:
        _require(settle_date is not None, "มียอดต้องคืนบริษัท กรุณาระบุวันที่โอนเงินคืน")
        return settle, settle_date
    return settle, None


def check_send_back(status, *, review_remark):
    _require_status(status, (AWAITING_REVIEW,), "ส่งกลับแก้ไข")
    _require(bool(review_remark and review_remark.strip()), "กรุณาระบุเหตุผลที่ส่งกลับ")


def check_fresh(expected, actual):
    """Optimistic check: the clearing Finance reviewed must be the one stored now."""
    if expected is not None and actual is not None and expected != actual:
        raise InvalidTransition("ข้อมูลเคลียร์ถูกแก้ไขหลังจากเปิดหน้านี้ กรุณารีเฟรชหน้าจอ")


def check_confirm(status, *, settle_amount, settle_date):
    _require_status(status, (AWAITING_REVIEW,), "ยืนยันปิดรายการ")
    if settle_amount is not None and Decimal(settle_amount) < 0:
        _require(settle_date is not None, "มียอดเบิกเพิ่ม กรุณาระบุวันที่การเงินโอนเงินเพิ่ม")
        return settle_date
    return None


def parse_id_list(raw):
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def is_finance_user(department_id, employee_id, dept_ids: Iterable[int], employee_ids: Iterable[str]) -> bool:
    if department_id is not None and department_id in set(dept_ids):
        return True
    return employee_id is not None and employee_id in set(employee_ids)


# key → (question_name, accepted question types when the name is missing; () = by name only)
_REQUEST_FIELDS = {
    "purpose": ("adv_purpose", ("longtext", "text")),
    "amount": ("adv_amount", ("number",)),
    "use_date": ("adv_use_date", ("datetime", "date")),
    "cost_center": ("adv_cost_center", ()),
    "bank": ("adv_bank", ()),
    "account_no": ("adv_account_no", ()),
    "account_name": ("adv_account_name", ()),
}
_ROW_VALUE_KEY = {"purpose": "text", "amount": "number", "use_date": "date", "cost_center": "text",
                  "bank": "text", "account_no": "text", "account_name": "text"}


def pick_request_values(rows):
    """rows: dicts with keys name, type, sort_order, text, number, date (one per answered question)."""
    ordered = sorted(rows, key=lambda r: r.get("sort_order") or 0)
    picked = {}
    for key, (name, types) in _REQUEST_FIELDS.items():
        row = next((r for r in ordered if r.get("name") == name), None)
        if row is None:
            row = next((r for r in ordered if r.get("type") in types), None)
        picked[key] = row.get(_ROW_VALUE_KEY[key]) if row else None
    return picked


def _jsonable(value):
    if isinstance(value, date):  # datetime is a subclass of date
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def diff_fields(before: Mapping, after: Mapping):
    changes = {}
    for field, new in after.items():
        old = before.get(field)
        if old != new:
            changes[field] = [_jsonable(old), _jsonable(new)]
    return changes


def check_use_date(use_date, today):
    """use_date: date | datetime | None. None → no check (required-ness is enforced elsewhere)."""
    if use_date is None:
        return
    day = use_date.date() if isinstance(use_date, datetime) else use_date
    if day < today:
        raise AdvanceRuleError(USE_DATE_MESSAGE)


def find_use_date(questions, values):
    """questions: dicts {id, name, type, sort_order}; values: dicts {question_id, value_date}.
    Picks the adv_use_date question by name, falling back to the first datetime/date question
    by sort_order, and returns its value as a date (accepts date, datetime or 'YYYY-MM-DD…' str), else None."""
    ordered = sorted(questions, key=lambda q: q.get("sort_order") or 0)
    question = next((q for q in ordered if q.get("name") == "adv_use_date"), None)
    if question is None:
        question = next((q for q in ordered if q.get("type") in ("datetime", "date")), None)
    if question is None:
        return None
    row = next((v for v in values if v.get("question_id") == question.get("id")), None)
    if row is None:
        return None
    raw = row.get("value_date")
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw).date()
        except ValueError:
            return None
    return None


# ---------------------------- payee bank account (spec v2 §4.2) ----------------------------
# value → (Thai label, allowed digit counts). Keep identical to menait-service lib/finance/bank.ts.
DEFAULT_ACCOUNT_DIGITS = (10, 11, 12)
BANKS = {
    "BBL": ("ธนาคารกรุงเทพ", (10,)),
    "KBANK": ("ธนาคารกสิกรไทย", (10,)),
    "KTB": ("ธนาคารกรุงไทย", (10,)),
    "SCB": ("ธนาคารไทยพาณิชย์", (10,)),
    "BAY": ("ธนาคารกรุงศรีอยุธยา", (10,)),
    "TTB": ("ธนาคารทหารไทยธนชาต", (10,)),
    "GSB": ("ธนาคารออมสิน", (12,)),
    "BAAC": ("ธ.ก.ส.", (12,)),
    "GHB": ("ธนาคารอาคารสงเคราะห์", (12,)),
    "UOB": ("ธนาคารยูโอบี", DEFAULT_ACCOUNT_DIGITS),
    "CIMBT": ("ธนาคารซีไอเอ็มบี ไทย", DEFAULT_ACCOUNT_DIGITS),
    "LHB": ("ธนาคารแลนด์ แอนด์ เฮ้าส์", DEFAULT_ACCOUNT_DIGITS),
    "KKP": ("ธนาคารเกียรตินาคินภัทร", DEFAULT_ACCOUNT_DIGITS),
    "TISCO": ("ธนาคารทิสโก้", DEFAULT_ACCOUNT_DIGITS),
    "ICBCT": ("ธนาคารไอซีบีซี (ไทย)", DEFAULT_ACCOUNT_DIGITS),
    "IBANK": ("ธนาคารอิสลามแห่งประเทศไทย", DEFAULT_ACCOUNT_DIGITS),
}
_ASCII_DIGITS = re.compile(r"[0-9]+")


def normalize_account_no(raw) -> str:
    return re.sub(r"[\s-]", "", str(raw)) if raw is not None else ""


def bank_label(value):
    if not value:
        return None
    return BANKS.get(value, (value,))[0]


def check_account_no(bank, raw) -> str:
    label, counts = BANKS.get(bank or "", (bank or "ธนาคาร", DEFAULT_ACCOUNT_DIGITS))
    digits = normalize_account_no(raw)
    if not _ASCII_DIGITS.fullmatch(digits) or len(digits) not in counts:
        count_text = f"{counts[0]}" if len(counts) == 1 else f"{counts[0]}–{counts[-1]}"
        raise AdvanceRuleError(f"เลขที่บัญชีไม่ถูกต้อง: {label} ต้องเป็นตัวเลข {count_text} หลัก")
    return digits


def submitted_value(questions, values, name, types, key):
    """questions: dicts {id, name, type, sort_order}; values: dicts {question_id, value_text, value_number,
    value_date}. Picks the question by name, else the first by sort_order whose type is in `types`, and
    returns that value's `key` (or None)."""
    ordered = sorted(questions, key=lambda q: q.get("sort_order") or 0)
    question = next((q for q in ordered if q.get("name") == name), None)
    if question is None:
        question = next((q for q in ordered if q.get("type") in types), None)
    if question is None:
        return None
    row = next((v for v in values if v.get("question_id") == question.get("id")), None)
    return row.get(key) if row else None
