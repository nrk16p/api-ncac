from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from schemas.finance_schema import ClearIn, PayIn


def test_blank_strings_become_none():
    body = PayIn(action_by="680001", acc_code="110103", voucher_no="  ", amount_paid="1000",
                 transfer_date="2026-07-09")
    assert body.voucher_no is None
    assert body.amount_paid == Decimal("1000")
    assert body.transfer_date == date(2026, 7, 9)


def test_blank_action_by_rejected():
    with pytest.raises(ValidationError):
        ClearIn(action_by=" ", clear_date="2026-07-15", amount_actual="10")


def test_more_than_two_decimals_rejected():
    with pytest.raises(ValidationError):
        PayIn(action_by="680001", acc_code="110103", amount_paid="10.123", transfer_date="2026-07-09")
