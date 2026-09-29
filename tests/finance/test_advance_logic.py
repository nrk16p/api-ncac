from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from services.finance import advance_logic as L

TODAY = date(2026, 9, 28)


class TestDeriveStatus:
    def test_in_progress_is_pending_approval(self):
        assert L.derive_status("In Progress", None, None, TODAY) == (L.PENDING_APPROVAL, False)

    def test_rejected(self):
        assert L.derive_status("Rejected", None, None, TODAY) == (L.REJECTED, False)

    def test_approved_without_fin_row_awaits_voucher(self):
        assert L.derive_status("Approved", None, None, TODAY) == (L.AWAITING_VOUCHER, False)

    def test_vouchered_awaits_payment(self):
        assert L.derive_status("Approved", L.FIN_VOUCHERED, None, TODAY) == (L.AWAITING_PAYMENT, False)

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
        for code in (L.PENDING_APPROVAL, L.REJECTED, L.AWAITING_VOUCHER, L.AWAITING_PAYMENT, L.AWAITING_CLEARING,
                     L.SENT_BACK, L.AWAITING_REVIEW, L.CLOSED):
            assert L.STATUS_LABELS[code]
        assert L.STATUS_LABELS[L.AWAITING_VOUCHER] == "รอตั้งเบิกทำจ่าย"

    def test_awaiting_review_label(self):
        assert L.STATUS_LABELS[L.AWAITING_REVIEW] == "รอบัญชีตรวจ"


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
        assert self._pay(L.AWAITING_CLEARING, is_edit=True) == date(2026, 7, 16)

    def test_create_ok_when_awaiting_payment_not_edit(self):
        assert self._pay(L.AWAITING_PAYMENT, is_edit=False) == date(2026, 7, 16)

    def test_rejects_stale_edit_when_still_awaiting_payment(self):
        with pytest.raises(L.InvalidTransition):
            self._pay(L.AWAITING_PAYMENT, is_edit=True)

    def test_rejects_create_when_already_awaiting_clearing(self):
        with pytest.raises(L.InvalidTransition):
            self._pay(L.AWAITING_CLEARING, is_edit=False)

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


class TestCheckVoucher:
    def test_create_on_awaiting_voucher(self):
        L.check_voucher(L.AWAITING_VOUCHER, voucher_date=date(2026, 9, 29))

    def test_edit_after_voucher_and_after_pay(self):
        L.check_voucher(L.AWAITING_PAYMENT, voucher_date=date(2026, 9, 29), is_edit=True)
        L.check_voucher(L.AWAITING_CLEARING, voucher_date=date(2026, 9, 29), is_edit=True)

    def test_stale_create_rejected(self):
        with pytest.raises(L.InvalidTransition, match="รายการนี้ถูกตั้งเบิกไปแล้ว"):
            L.check_voucher(L.AWAITING_PAYMENT, voucher_date=date(2026, 9, 29))

    def test_edit_without_voucher_rejected(self):
        with pytest.raises(L.InvalidTransition, match="ยังไม่มีข้อมูลการตั้งเบิกให้แก้ไข"):
            L.check_voucher(L.AWAITING_VOUCHER, voucher_date=date(2026, 9, 29), is_edit=True)

    def test_voucher_date_required(self):
        with pytest.raises(L.AdvanceRuleError, match="กรุณาระบุวันที่ตั้งเบิก"):
            L.check_voucher(L.AWAITING_VOUCHER, voucher_date=None)

    @pytest.mark.parametrize("status", ["PENDING_APPROVAL", "REJECTED", "AWAITING_REVIEW", "CLOSED", "SENT_BACK"])
    def test_wrong_status(self, status):
        with pytest.raises(L.InvalidTransition):
            L.check_voucher(status, voucher_date=date(2026, 9, 29), is_edit=True)


def test_pay_needs_voucher_first():
    with pytest.raises(L.InvalidTransition, match="รอตั้งเบิกทำจ่าย"):
        L.check_pay(L.AWAITING_VOUCHER, acc_active=True, amount_paid=100, transfer_date=date(2026, 10, 1),
                    clear_due_date=None)


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


class TestCheckFresh:
    def test_equal_aware_datetimes_pass(self):
        dt = datetime(2026, 7, 15, 3, 0, tzinfo=timezone.utc)
        assert L.check_fresh(dt, dt) is None

    def test_different_raises(self):
        expected = datetime(2026, 7, 15, 3, 0, tzinfo=timezone.utc)
        actual = datetime(2026, 7, 15, 4, 0, tzinfo=timezone.utc)
        with pytest.raises(L.InvalidTransition):
            L.check_fresh(expected, actual)

    def test_expected_none_passes(self):
        actual = datetime(2026, 7, 15, 3, 0, tzinfo=timezone.utc)
        assert L.check_fresh(None, actual) is None


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
        result = L.pick_request_values(self.ROWS)
        assert result["purpose"] == "ค่าเดินทาง"
        assert result["amount"] == Decimal("4840")
        assert result["use_date"] == datetime(2026, 7, 14)
        assert result["cost_center"] is None
        assert result["bank"] is None
        assert result["account_no"] is None
        assert result["account_name"] is None

    def test_falls_back_to_type_when_renamed(self):
        renamed = [dict(r, name=f"q{i}") for i, r in enumerate(self.ROWS)]
        assert L.pick_request_values(renamed)["amount"] == Decimal("4840")

    def test_missing_values_are_none(self):
        result = L.pick_request_values([])
        assert result["purpose"] is None
        assert result["amount"] is None
        assert result["use_date"] is None
        assert result["cost_center"] is None
        assert result["bank"] is None
        assert result["account_no"] is None
        assert result["account_name"] is None


class TestDiffFields:
    def test_only_changed_fields_serialized(self):
        before = {"amount_paid": Decimal("1000.00"), "voucher_no": "A", "transfer_date": date(2026, 7, 9)}
        after = {"amount_paid": Decimal("1000"), "voucher_no": "B", "transfer_date": date(2026, 7, 10)}
        assert L.diff_fields(before, after) == {
            "voucher_no": ["A", "B"], "transfer_date": ["2026-07-09", "2026-07-10"]}

    def test_new_row_lists_everything(self):
        assert L.diff_fields({}, {"amount_paid": Decimal("5")}) == {"amount_paid": [None, "5"]}


class TestCheckUseDate:
    def test_today_passes(self):
        assert L.check_use_date(TODAY, TODAY) is None

    def test_tomorrow_passes(self):
        assert L.check_use_date(TODAY + timedelta(days=1), TODAY) is None

    def test_yesterday_raises(self):
        with pytest.raises(L.AdvanceRuleError) as exc_info:
            L.check_use_date(TODAY - timedelta(days=1), TODAY)
        assert str(exc_info.value) == L.USE_DATE_MESSAGE

    def test_datetime_at_midnight_today_passes(self):
        assert L.check_use_date(datetime(2026, 9, 28, 0, 0), TODAY) is None

    def test_none_passes(self):
        assert L.check_use_date(None, TODAY) is None


class TestFindUseDate:
    QUESTIONS = [
        {"id": 1, "name": "adv_purpose", "type": "longtext", "sort_order": 1},
        {"id": 2, "name": "adv_amount", "type": "number", "sort_order": 2},
        {"id": 3, "name": "adv_use_date", "type": "datetime", "sort_order": 3},
    ]

    def test_picks_by_name(self):
        values = [{"question_id": 3, "value_date": date(2026, 9, 30)}]
        assert L.find_use_date(self.QUESTIONS, values) == date(2026, 9, 30)

    def test_falls_back_by_type_when_renamed(self):
        renamed = [dict(q, name=f"q{q['id']}") for q in self.QUESTIONS]
        values = [{"question_id": 3, "value_date": date(2026, 9, 30)}]
        assert L.find_use_date(renamed, values) == date(2026, 9, 30)

    def test_parses_date_string(self):
        values = [{"question_id": 3, "value_date": "2026-09-30"}]
        assert L.find_use_date(self.QUESTIONS, values) == date(2026, 9, 30)

    def test_parses_datetime_string(self):
        values = [{"question_id": 3, "value_date": "2026-09-30T00:00:00"}]
        assert L.find_use_date(self.QUESTIONS, values) == date(2026, 9, 30)

    def test_datetime_value(self):
        values = [{"question_id": 3, "value_date": datetime(2026, 9, 30, 13, 45)}]
        assert L.find_use_date(self.QUESTIONS, values) == date(2026, 9, 30)

    def test_none_when_no_matching_question(self):
        no_date_questions = [q for q in self.QUESTIONS if q["type"] != "datetime"]
        values = [{"question_id": 3, "value_date": date(2026, 9, 30)}]
        assert L.find_use_date(no_date_questions, values) is None

    def test_none_when_no_value(self):
        assert L.find_use_date(self.QUESTIONS, []) is None


class TestAccountNo:
    @pytest.mark.parametrize("bank,raw,expected", [
        ("KBANK", "123-4-56789-0", "1234567890"),
        ("SCB", " 123 456 7890 ", "1234567890"),
        ("GSB", "0200-1234-5678", "020012345678"),
        ("UOB", "12345678901", "12345678901"),
        ("OTHER", "123456789012", "123456789012"),
    ])
    def test_valid(self, bank, raw, expected):
        assert L.check_account_no(bank, raw) == expected

    @pytest.mark.parametrize("bank,raw,msg", [
        ("KBANK", "123456789", "เลขที่บัญชีไม่ถูกต้อง: ธนาคารกสิกรไทย ต้องเป็นตัวเลข 10 หลัก"),
        ("GSB", "1234567890", "เลขที่บัญชีไม่ถูกต้อง: ธนาคารออมสิน ต้องเป็นตัวเลข 12 หลัก"),
        ("UOB", "123456789", "เลขที่บัญชีไม่ถูกต้อง: ธนาคารยูโอบี ต้องเป็นตัวเลข 10–12 หลัก"),
        ("KBANK", "๑๒๓๔๕๖๗๘๙๐", "เลขที่บัญชีไม่ถูกต้อง: ธนาคารกสิกรไทย ต้องเป็นตัวเลข 10 หลัก"),
        ("KBANK", "12345abcde", "เลขที่บัญชีไม่ถูกต้อง: ธนาคารกสิกรไทย ต้องเป็นตัวเลข 10 หลัก"),
        ("KBANK", None, "เลขที่บัญชีไม่ถูกต้อง: ธนาคารกสิกรไทย ต้องเป็นตัวเลข 10 หลัก"),
    ])
    def test_invalid(self, bank, raw, msg):
        with pytest.raises(L.AdvanceRuleError) as exc:
            L.check_account_no(bank, raw)
        assert str(exc.value) == msg

    def test_bank_label(self):
        assert L.bank_label("KTB") == "ธนาคารกรุงไทย"
        assert L.bank_label("ZZZ") == "ZZZ"
        assert L.bank_label(None) is None


class TestRequestFieldsV2:
    def test_new_fields_by_name_only(self):
        rows = [
            {"name": "adv_purpose", "type": "longtext", "sort_order": 1, "text": "p", "number": None, "date": None},
            {"name": "adv_amount", "type": "number", "sort_order": 2, "text": None, "number": Decimal("1500"), "date": None},
            {"name": "adv_cost_center", "type": "dropdown", "sort_order": 4, "text": "ศลบ", "number": None, "date": None},
            {"name": "adv_bank", "type": "dropdown", "sort_order": 5, "text": "KBANK", "number": None, "date": None},
            {"name": "adv_account_no", "type": "text", "sort_order": 6, "text": "1234567890", "number": None, "date": None},
            {"name": "adv_account_name", "type": "text", "sort_order": 7, "text": "นาย ก", "number": None, "date": None},
        ]
        picked = L.pick_request_values(rows)
        assert (picked["cost_center"], picked["bank"], picked["account_no"], picked["account_name"]) == \
            ("ศลบ", "KBANK", "1234567890", "นาย ก")

    def test_old_advance_without_new_questions(self):
        picked = L.pick_request_values([
            {"name": "adv_purpose", "type": "longtext", "sort_order": 1, "text": "p", "number": None, "date": None},
        ])
        assert picked["cost_center"] is None and picked["bank"] is None and picked["account_no"] is None

    def test_account_name_never_falls_back_to_purpose(self):
        picked = L.pick_request_values([
            {"name": "something_else", "type": "text", "sort_order": 1, "text": "x", "number": None, "date": None},
        ])
        assert picked["account_name"] is None


class TestSubmittedValue:
    QS = [{"id": 1, "name": "adv_amount", "type": "number", "sort_order": 2},
          {"id": 2, "name": "adv_bank", "type": "dropdown", "sort_order": 5}]

    def test_by_name(self):
        values = [{"question_id": 2, "value_text": "SCB", "value_number": None, "value_date": None}]
        assert L.submitted_value(self.QS, values, "adv_bank", (), "value_text") == "SCB"

    def test_fallback_by_type(self):
        qs = [{"id": 9, "name": "amount_old", "type": "number", "sort_order": 1}]
        values = [{"question_id": 9, "value_text": None, "value_number": Decimal("10"), "value_date": None}]
        assert L.submitted_value(qs, values, "adv_amount", ("number",), "value_number") == Decimal("10")

    def test_missing(self):
        assert L.submitted_value(self.QS, [], "adv_bank", (), "value_text") is None
        assert L.submitted_value([], [], "adv_bank", (), "value_text") is None


def test_clear_date_message_renamed():
    with pytest.raises(L.AdvanceRuleError, match="กรุณาระบุวันที่ส่งเอกสารเคลียร์"):
        L.check_clear(L.AWAITING_CLEARING, is_owner=True, amount_paid=100, clear_date=None,
                      amount_actual=100, settle_date=None)
