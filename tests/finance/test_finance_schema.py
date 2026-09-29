from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from schemas.finance_schema import ClearIn, PayIn


def test_blank_strings_become_none():
    body = PayIn(action_by="680001", acc_code="110103", payment_doc_no="  ", amount_paid="1000",
                 transfer_date="2026-07-09")
    assert body.payment_doc_no is None
    assert body.amount_paid == Decimal("1000")
    assert body.transfer_date == date(2026, 7, 9)


def test_blank_action_by_rejected():
    with pytest.raises(ValidationError):
        ClearIn(action_by=" ", clear_date="2026-07-15", amount_actual="10")


def test_more_than_two_decimals_rejected():
    with pytest.raises(ValidationError):
        PayIn(action_by="680001", acc_code="110103", amount_paid="10.123", transfer_date="2026-07-09")


def test_pay_in_v2_acc_code_optional_no_voucher_fields():
    body = PayIn(action_by="670108", amount_paid="3000", transfer_date="2026-10-01")
    assert body.acc_code is None
    assert not hasattr(body, "voucher_date")


def test_voucher_in_requires_date():
    from schemas.finance_schema import VoucherIn
    assert VoucherIn(action_by="670108", voucher_date="2026-09-29").voucher_no is None
    with pytest.raises(ValidationError):
        VoucherIn(action_by="670108")


def test_confirm_in_clear_doc_no_optional_and_blank_becomes_none():
    from schemas.finance_schema import ConfirmIn
    assert ConfirmIn(action_by="a").clear_doc_no is None
    assert ConfirmIn(action_by="a", clear_doc_no="  ").clear_doc_no is None
    assert ConfirmIn(action_by="a", clear_doc_no=" R1 ").clear_doc_no == "R1"


def test_reject_voucher_in_requires_remark():
    from schemas.finance_schema import RejectVoucherIn
    assert RejectVoucherIn(action_by="680001", remark="ผิด").remark == "ผิด"
    with pytest.raises(ValidationError):
        RejectVoucherIn(action_by="680001")
