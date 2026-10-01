from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class _Body(BaseModel):
    @field_validator("*", mode="before")
    @classmethod
    def _blank_to_none(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class AccountCreate(_Body):
    action_by: str
    acc_code: str = Field(max_length=20)
    acc_name: str = Field(max_length=255)
    acc_name_en: Optional[str] = Field(default=None, max_length=255)


class AccountUpdate(_Body):
    action_by: str
    acc_name: Optional[str] = Field(default=None, max_length=255)
    acc_name_en: Optional[str] = Field(default=None, max_length=255)
    is_active: Optional[bool] = None


class PayIn(_Body):
    action_by: str
    acc_code: Optional[str] = None
    payment_doc_no: Optional[str] = Field(default=None, max_length=50)
    purpose: Optional[str] = None
    amount_paid: Decimal = Field(max_digits=12, decimal_places=2)
    transfer_date: date
    clear_due_date: Optional[date] = None
    is_edit: bool = False


class VoucherIn(_Body):
    action_by: str
    voucher_no: Optional[str] = Field(default=None, max_length=50)
    voucher_date: date
    is_edit: bool = False


class ClearItemIn(_Body):
    expense_date: date
    vehicle: Optional[str] = Field(default=None, max_length=50)
    has_receipt: bool = True
    description: str = Field(max_length=255)
    amount_before_vat: Decimal = Field(max_digits=12, decimal_places=2)
    vat_amount: Decimal = Field(default=Decimal("0"), max_digits=12, decimal_places=2)
    wht_amount: Decimal = Field(default=Decimal("0"), max_digits=12, decimal_places=2)


class ClearIn(_Body):
    action_by: str
    clear_date: date
    # Ignored: the server computes amount_actual = sum of net_amount over items.
    amount_actual: Optional[Decimal] = Field(default=None, max_digits=12, decimal_places=2)
    items: list[ClearItemIn] = Field(min_length=1)
    clear_doc_no: Optional[str] = Field(default=None, max_length=100)
    settle_date: Optional[date] = None
    remark: Optional[str] = None


class SendBackIn(_Body):
    action_by: str
    review_remark: str
    expected_clear_submitted_at: Optional[datetime] = None


class RejectVoucherIn(_Body):
    action_by: str
    remark: str


class ConfirmIn(_Body):
    action_by: str
    clear_doc_no: Optional[str] = Field(default=None, max_length=100)
    settle_date: Optional[date] = None
    expected_clear_submitted_at: Optional[datetime] = None
