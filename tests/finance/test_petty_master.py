import pytest
from pydantic import ValidationError

from schemas.finance_petty_schema import ExpenseTypeUpdate, FundCreate, FundUpdate


class TestFundCreate:
    def test_minimal(self):
        f = FundCreate(branch_code="03", division_code="B02", holder_employee_id="12345")
        assert f.is_active is True
        assert f.fund_limit is None

    def test_branch_code_is_two_digits(self):
        with pytest.raises(ValidationError):
            FundCreate(branch_code="3", division_code="B02", holder_employee_id="12345")

    def test_holder_is_required(self):
        with pytest.raises(ValidationError):
            FundCreate(branch_code="03", division_code="B02")

    def test_negative_limit_rejected(self):
        with pytest.raises(ValidationError):
            FundCreate(branch_code="03", division_code="B02", holder_employee_id="1", fund_limit=-1)


class TestExpenseTypeUpdate:
    def test_spec_must_be_object(self):
        with pytest.raises(ValidationError):
            ExpenseTypeUpdate(name_th="x", spec=[1, 2, 3])

    def test_item_based_spec_requires_item_fields(self):
        with pytest.raises(ValidationError) as exc:
            ExpenseTypeUpdate(name_th="ค่าชั่งน้ำหนัก", spec={"item_based": True, "item_fields": []})
        assert "item_fields" in str(exc.value)

    def test_weigh_spec_accepted(self):
        spec = {
            "item_based": True,
            "item_fields": [{"key": "ref_no", "label": "เลขที่บัตรชั่ง", "type": "text", "required": True}],
            "attachments": [{"key": "weigh_slip", "label": "ใบชั่ง", "required": True, "max": 10}],
            "duplicate_check": {
                "scope": "item",
                "hard": {"keys": ["ref_no", "truck_code", "truck_plate"], "days": 365, "action": "block"},
                "soft": {"keys": ["event_date", "truck"], "days": 7, "action": "warn"},
            },
        }
        assert ExpenseTypeUpdate(name_th="ค่าชั่งน้ำหนัก", spec=spec).spec["item_based"] is True

    def test_unknown_attachment_shape_rejected(self):
        with pytest.raises(ValidationError):
            ExpenseTypeUpdate(name_th="x", spec={"attachments": [{"label": "ไม่มี key"}]})


class TestFundUpdate:
    def test_all_fields_optional(self):
        assert FundUpdate().model_dump(exclude_unset=True) == {}

    def test_deactivate(self):
        assert FundUpdate(is_active=False).is_active is False
