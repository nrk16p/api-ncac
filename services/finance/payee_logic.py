"""Finance payee account master — pure rules (no DB). K-Bank only, exactly 10 digits."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

from services.finance.advance_logic import AdvanceRuleError, InvalidTransition, _require, normalize_account_no

PAYEE_BANK = "KBANK"
PAYEE_SELF = "SELF"
PAYEE_SUPPLIER = "SUPPLIER"

KBANK_DIGITS = 10
NAME_MAX = 150
_ASCII_DIGITS = re.compile(r"[0-9]+")


def check_kbank_account(raw) -> str:
    digits = normalize_account_no(raw)
    _require(_ASCII_DIGITS.fullmatch(digits) and len(digits) == KBANK_DIGITS, "เลขที่บัญชีกสิกรไทยต้องมี 10 หลัก")
    return digits


def check_account_name(name) -> str:
    text = name.strip() if isinstance(name, str) else ""
    _require(text, "กรุณาระบุชื่อบัญชี")
    _require(len(text) <= NAME_MAX, f"ชื่อบัญชีต้องไม่เกิน {NAME_MAX} ตัวอักษร")
    return text


def check_request_open(status) -> None:
    if status != "PENDING":
        raise InvalidTransition("คำขอนี้ไม่อยู่ในสถานะรอตรวจสอบ")


def check_reject_remark(remark) -> str:
    text = remark.strip() if isinstance(remark, str) else ""
    _require(text, "กรุณาระบุเหตุผลที่ไม่อนุมัติ")
    return text


def check_master_status(status) -> str:
    _require(status in ("ACTIVE", "INACTIVE"), "สถานะบัญชีไม่ถูกต้อง")
    return status


def _json_safe(value):
    if isinstance(value, (Decimal, date, datetime)):
        return str(value)
    return value


def diff_pairs(before: dict, after: dict) -> dict:
    """{key: [before, after]} for keys whose value changed (keys in either dict)."""
    out = {}
    for key in list(before) + [k for k in after if k not in before]:
        b, a = before.get(key), after.get(key)
        if b != a:
            out[key] = [_json_safe(b), _json_safe(a)]
    return out


def resolve_payee(payee_type, master, submitted=None):
    """SELF -> the master's values (client input is ignored); SUPPLIER -> None (caller keeps its own checks)."""
    if payee_type not in (PAYEE_SELF, PAYEE_SUPPLIER):
        raise AdvanceRuleError("กรุณาเลือกบัญชีรับเงิน")
    if payee_type == PAYEE_SUPPLIER:
        return None
    if master is None or master.status != "ACTIVE":
        raise AdvanceRuleError("ยังไม่มีบัญชีรับเงินที่บัญชีอนุมัติ — กรุณาขอเพิ่มบัญชีรับเงิน")
    return {"bank": PAYEE_BANK, "account_no": master.account_no, "account_name": master.account_name}
