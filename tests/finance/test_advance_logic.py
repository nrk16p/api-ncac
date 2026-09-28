from datetime import date, datetime
from decimal import Decimal

import pytest

from services.finance import advance_logic as L

TODAY = date(2026, 9, 28)


class TestDeriveStatus:
    def test_in_progress_is_pending_approval(self):
        assert L.derive_status("In Progress", None, None, TODAY) == (L.PENDING_APPROVAL, False)

    def test_rejected(self):
        assert L.derive_status("Rejected", None, None, TODAY) == (L.REJECTED, False)

    def test_approved_without_fin_row_awaits_payment(self):
        assert L.derive_status("Approved", None, None, TODAY) == (L.AWAITING_PAYMENT, False)

    def test_paid_due_today_is_not_overdue(self):
        assert L.derive_status("Approved", L.FIN_PAID, date(2026, 9, 28), TODAY) == (L.AWAITING_CLEARING, False)

    def test_paid_due_yesterday_is_overdue(self):
        assert L.derive_status("Approved", L.FIN_PAID, date(2026, 9, 27), TODAY) == (L.AWAITING_CLEARING, True)

    def test_sent_back_past_due_is_overdue(self):
        assert L.derive_status("Approved", L.FIN_SENT_BACK, date(2026, 9, 1), TODAY) == (L.SENT_BACK, True)

    def test_submitted_clearing_is_never_overdue(self):
        assert L.derive_status("Approved", L.FIN_CLEARING_SUBMITTED, date(2026, 9, 1), TODAY) == (L.AWAITING_REVIEW, False)

    def test_closed(self):
        assert L.derive_status("Approved", L.FIN_CLOSED, date(2026, 9, 1), TODAY) == (L.CLOSED, False)

    def test_labels(self):
        assert L.STATUS_LABELS[L.AWAITING_CLEARING] == "จ่ายแล้วรอเคลียร์"
        for code in (L.PENDING_APPROVAL, L.REJECTED, L.AWAITING_PAYMENT, L.AWAITING_CLEARING,
                     L.SENT_BACK, L.AWAITING_REVIEW, L.CLOSED):
            assert L.STATUS_LABELS[code]


class TestDueDateAndSettle:
    def test_default_due_is_plus_7(self):
        assert L.default_due_date(date(2026, 6, 25)) == date(2026, 7, 2)

    def test_default_due_crosses_month_end(self):
        assert L.default_due_date(date(2026, 6, 28)) == date(2026, 7, 5)

    def test_settle_return(self):
        assert L.compute_settle_amount(Decimal("1000"), Decimal("800.50")) == Decimal("199.50")

    def test_settle_extra(self):
        assert L.compute_settle_amount(Decimal("1000"), Decimal("1250")) == Decimal("-250.00")

    def test_settle_exact(self):
        assert L.compute_settle_amount(Decimal("1000.00"), Decimal("1000")) == Decimal("0.00")


class TestCheckPay:
    def _pay(self, status=L.AWAITING_PAYMENT, **kw):
        args = dict(acc_active=True, amount_paid=Decimal("1000"),
                    transfer_date=date(2026, 7, 9), clear_due_date=None)
        args.update(kw)
        return L.check_pay(status, **args)

    def test_defaults_due_date(self):
        assert self._pay() == date(2026, 7, 16)

    def test_keeps_explicit_due_date(self):
        assert self._pay(clear_due_date=date(2026, 7, 20)) == date(2026, 7, 20)

    def test_edit_allowed_while_awaiting_clearing(self):
        assert self._pay(L.AWAITING_CLEARING) == date(2026, 7, 16)

    @pytest.mark.parametrize("status", [L.PENDING_APPROVAL, L.REJECTED, L.SENT_BACK, L.AWAITING_REVIEW, L.CLOSED])
    def test_rejects_other_statuses(self, status):
        with pytest.raises(L.InvalidTransition):
            self._pay(status)

    def test_requires_active_account(self):
        with pytest.raises(L.AdvanceRuleError):
            self._pay(acc_active=False)

    def test_rejects_negative_amount(self):
        with pytest.raises(L.AdvanceRuleError):
            self._pay(amount_paid=Decimal("-1"))

    def test_requires_transfer_date(self):
        with pytest.raises(L.AdvanceRuleError):
            self._pay(transfer_date=None)

    def test_rejects_due_before_transfer(self):
        with pytest.raises(L.AdvanceRuleError):
            self._pay(clear_due_date=date(2026, 7, 1))


class TestCheckClear:
    def _clear(self, status=L.AWAITING_CLEARING, **kw):
        args = dict(is_owner=True, amount_paid=Decimal("1000"), clear_date=date(2026, 7, 15),
                    amount_actual=Decimal("800"), settle_date=date(2026, 7, 15))
        args.update(kw)
        return L.check_clear(status, **args)

    def test_return_keeps_requester_settle_date(self):
        assert self._clear() == (Decimal("200.00"), date(2026, 7, 15))

    def test_return_requires_settle_date(self):
        with pytest.raises(L.AdvanceRuleError):
            self._clear(settle_date=None)

    def test_extra_ignores_requester_settle_date(self):
        assert self._clear(amount_actual=Decimal("1200")) == (Decimal("-200.00"), None)

    def test_exact_amount_needs_no_date(self):
        assert self._clear(amount_actual=Decimal("1000"), settle_date=None) == (Decimal("0.00"), None)

    def test_non_owner_is_forbidden(self):
        with pytest.raises(L.NotAllowed):
            self._clear(is_owner=False)

    @pytest.mark.parametrize("status", [L.SENT_BACK, L.AWAITING_REVIEW])
    def test_resubmit_and_edit_allowed(self, status):
        assert self._clear(status)[0] == Decimal("200.00")

    @pytest.mark.parametrize("status", [L.PENDING_APPROVAL, L.REJECTED, L.AWAITING_PAYMENT, L.CLOSED])
    def test_rejects_other_statuses(self, status):
        with pytest.raises(L.InvalidTransition):
            self._clear(status)

    def test_requires_clear_date(self):
        with pytest.raises(L.AdvanceRuleError):
            self._clear(clear_date=None)

    def test_rejects_negative_actual(self):
        with pytest.raises(L.AdvanceRuleError):
            self._clear(amount_actual=Decimal("-5"))


class TestReview:
    def test_send_back_requires_remark(self):
        with pytest.raises(L.AdvanceRuleError):
            L.check_send_back(L.AWAITING_REVIEW, review_remark="   ")

    def test_send_back_ok(self):
        assert L.check_send_back(L.AWAITING_REVIEW, review_remark="ใบเสร็จไม่ครบ") is None

    def test_send_back_wrong_status(self):
        with pytest.raises(L.InvalidTransition):
            L.check_send_back(L.AWAITING_CLEARING, review_remark="x")

    def test_confirm_return_needs_no_date(self):
        assert L.check_confirm(L.AWAITING_REVIEW, settle_amount=Decimal("200"), settle_date=None) is None

    def test_confirm_extra_requires_date(self):
        with pytest.raises(L.AdvanceRuleError):
            L.check_confirm(L.AWAITING_REVIEW, settle_amount=Decimal("-200"), settle_date=None)

    def test_confirm_extra_returns_date(self):
        assert L.check_confirm(L.AWAITING_REVIEW, settle_amount=Decimal("-200"),
                               settle_date=date(2026, 7, 20)) == date(2026, 7, 20)

    def test_confirm_wrong_status(self):
        with pytest.raises(L.InvalidTransition):
            L.check_confirm(L.SENT_BACK, settle_amount=Decimal("0"), settle_date=None)


class TestFinanceRole:
    def test_parse_id_list(self):
        assert L.parse_id_list(" 4, 6 ,,") == ["4", "6"]
        assert L.parse_id_list(None) == []

    def test_department_member(self):
        assert L.is_finance_user(4, "680001", [4, 6], [])

    def test_employee_override(self):
        assert L.is_finance_user(21, "680043", [4, 6], ["680043"])

    def test_not_finance(self):
        assert not L.is_finance_user(21, "680001", [4, 6], [])
        assert not L.is_finance_user(None, None, [4, 6], [])


class TestPickRequestValues:
    ROWS = [
        {"name": "adv_purpose", "type": "longtext", "sort_order": 1, "text": "ค่าเดินทาง", "number": None, "date": None},
        {"name": "adv_amount", "type": "number", "sort_order": 2, "text": None, "number": Decimal("4840"), "date": None},
        {"name": "adv_use_date", "type": "datetime", "sort_order": 3, "text": None, "number": None,
         "date": datetime(2026, 7, 14)},
    ]

    def test_by_question_name(self):
        assert L.pick_request_values(self.ROWS) == {
            "purpose": "ค่าเดินทาง", "amount": Decimal("4840"), "use_date": datetime(2026, 7, 14)}

    def test_falls_back_to_type_when_renamed(self):
        renamed = [dict(r, name=f"q{i}") for i, r in enumerate(self.ROWS)]
        assert L.pick_request_values(renamed)["amount"] == Decimal("4840")

    def test_missing_values_are_none(self):
        assert L.pick_request_values([]) == {"purpose": None, "amount": None, "use_date": None}


class TestDiffFields:
    def test_only_changed_fields_serialized(self):
        before = {"amount_paid": Decimal("1000.00"), "voucher_no": "A", "transfer_date": date(2026, 7, 9)}
        after = {"amount_paid": Decimal("1000"), "voucher_no": "B", "transfer_date": date(2026, 7, 10)}
        assert L.diff_fields(before, after) == {
            "voucher_no": ["A", "B"], "transfer_date": ["2026-07-09", "2026-07-10"]}

    def test_new_row_lists_everything(self):
        assert L.diff_fields({}, {"amount_paid": Decimal("5")}) == {"amount_paid": [None, "5"]}
