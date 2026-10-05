from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from services.finance import payee_logic as p
from services.finance.advance_logic import AdvanceRuleError, InvalidTransition


def test_constants():
    assert (p.PAYEE_BANK, p.PAYEE_SELF, p.PAYEE_SUPPLIER) == ("KBANK", "SELF", "SUPPLIER")


@pytest.mark.parametrize("raw,expected", [("1234567890", "1234567890"), ("123-4-56789-0", "1234567890"),
                                          (" 123 456 7890 ", "1234567890")])
def test_kbank_ok(raw, expected):
    assert p.check_kbank_account(raw) == expected


@pytest.mark.parametrize("raw", ["123456789", "12345678901", "12345abcde", None, "", "---", "١٢٣٤٥٦٧٨٩٠"])
def test_kbank_bad(raw):
    with pytest.raises(AdvanceRuleError, match="^เลขที่บัญชีกสิกรไทยต้องมี 10 หลัก$"):
        p.check_kbank_account(raw)


def test_account_name():
    assert p.check_account_name("  นาย ก  ") == "นาย ก"
    assert p.check_account_name("ก" * 150) == "ก" * 150
    for bad in ("", "   ", None):
        with pytest.raises(AdvanceRuleError, match="^กรุณาระบุชื่อบัญชี$"):
            p.check_account_name(bad)
    with pytest.raises(AdvanceRuleError):
        p.check_account_name("ก" * 151)


def test_request_open():
    p.check_request_open("PENDING")
    for s in ("APPROVED", "REJECTED", "CANCELLED", None):
        with pytest.raises(InvalidTransition, match="^คำขอนี้ไม่อยู่ในสถานะรอตรวจสอบ$"):
            p.check_request_open(s)


def test_reject_remark():
    assert p.check_reject_remark("  ไม่ตรง ") == "ไม่ตรง"
    for bad in ("", "  ", None):
        with pytest.raises(AdvanceRuleError, match="^กรุณาระบุเหตุผลที่ไม่อนุมัติ$"):
            p.check_reject_remark(bad)


def test_master_status():
    assert p.check_master_status("ACTIVE") == "ACTIVE"
    assert p.check_master_status("INACTIVE") == "INACTIVE"
    for bad in ("PENDING", "active", None, ""):
        with pytest.raises(AdvanceRuleError):
            p.check_master_status(bad)


def test_diff_pairs():
    assert p.diff_pairs({"a": 1, "b": 2}, {"a": 1, "b": 3}) == {"b": [2, 3]}
    assert p.diff_pairs({}, {"a": "x"}) == {"a": [None, "x"]}
    assert p.diff_pairs({"a": "x"}, {}) == {"a": ["x", None]}
    assert p.diff_pairs({"a": 1}, {"a": 1}) == {}
    assert p.diff_pairs({"a": Decimal("1.50")}, {"a": date(2026, 10, 5)}) == {"a": ["1.50", "2026-10-05"]}


def _m(status="ACTIVE"):
    return SimpleNamespace(status=status, account_no="1234567890", account_name="นาย ก")


@pytest.mark.parametrize("bad", [None, "", "OTHER", "self"])
def test_resolve_payee_bad_type(bad):
    with pytest.raises(AdvanceRuleError, match="^กรุณาเลือกบัญชีรับเงิน$"):
        p.resolve_payee(bad, _m(), {})


def test_resolve_payee_self_uses_master_ignoring_submitted():
    got = p.resolve_payee("SELF", _m(), {"bank": "SCB", "account_no": "999", "account_name": "evil"})
    assert got == {"bank": "KBANK", "account_no": "1234567890", "account_name": "นาย ก"}


@pytest.mark.parametrize("master", [None, _m("INACTIVE")])
def test_resolve_payee_self_without_active_master(master):
    with pytest.raises(AdvanceRuleError, match="^ยังไม่มีบัญชีรับเงินที่บัญชีอนุมัติ — กรุณาขอเพิ่มบัญชีรับเงิน$"):
        p.resolve_payee("SELF", master, {})


def test_resolve_payee_supplier_returns_none():
    assert p.resolve_payee("SUPPLIER", None, {}) is None
