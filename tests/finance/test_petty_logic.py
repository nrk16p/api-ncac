from datetime import date
from decimal import Decimal

import pytest

from services.finance import petty_logic as L


class TestTransitions:
    """flow v2 (29/10/2026): เจ้าของเงินสดอนุมัติ+จ่าย → ส่งเป็นรอบ → หัวหน้าอนุมัติเคลียร์ → บัญชีตรวจ → ทำจ่ายทั้งรอบ"""

    def test_submit_to_approved(self):
        assert L.assert_transition("PENDING_APPROVAL", "APPROVED") is None

    def test_returned_can_be_resubmitted(self):
        assert L.assert_transition("RETURNED", "PENDING_APPROVAL") is None

    def test_approved_goes_into_a_round(self):
        assert L.assert_transition("APPROVED", "CLEAR_PENDING") is None

    def test_head_approval_sends_to_accounting(self):
        assert L.assert_transition("CLEAR_PENDING", "AWAITING_REVIEW") is None

    def test_head_rejection_returns_to_the_holder(self):
        assert L.assert_transition("CLEAR_PENDING", "APPROVED") is None

    def test_accounting_passes_or_sends_back(self):
        assert L.assert_transition("AWAITING_REVIEW", "REVIEWED") is None
        assert L.assert_transition("AWAITING_REVIEW", "SENT_BACK") is None

    def test_sent_back_goes_straight_back_to_accounting(self):
        assert L.assert_transition("SENT_BACK", "AWAITING_REVIEW") is None

    def test_paying_the_round_closes_a_reviewed_claim(self):
        assert L.assert_transition("REVIEWED", "CLOSED") is None

    def test_cannot_approve_twice(self):
        with pytest.raises(L.PettyRuleError) as exc:
            L.assert_transition("APPROVED", "APPROVED")
        assert exc.value.http_status == 409
        assert "อนุมัติจ่ายแล้ว" in str(exc.value)

    def test_cannot_skip_the_head(self):
        with pytest.raises(L.PettyRuleError):
            L.assert_transition("APPROVED", "AWAITING_REVIEW")

    def test_cannot_close_before_accounting_passes(self):
        with pytest.raises(L.PettyRuleError):
            L.assert_transition("AWAITING_REVIEW", "CLOSED")

    def test_closed_is_terminal(self):
        with pytest.raises(L.PettyRuleError):
            L.assert_transition("CLOSED", "AWAITING_REVIEW")

    def test_old_transferred_status_no_longer_moves(self):
        with pytest.raises(L.PettyRuleError):
            L.assert_transition("TRANSFERRED", "AWAITING_REVIEW")

    def test_unknown_status_is_rejected(self):
        with pytest.raises(L.PettyRuleError):
            L.assert_transition("WHATEVER", "APPROVED")


class TestLabels:
    def test_eight_statuses(self):
        assert L.STATUSES == ("PENDING_APPROVAL", "RETURNED", "APPROVED", "CLEAR_PENDING",
                              "AWAITING_REVIEW", "SENT_BACK", "REVIEWED", "CLOSED")

    def test_labels_cover_every_status_and_the_old_one(self):
        assert set(L.STATUS_LABELS) == set(L.STATUSES) | {"TRANSFERRED"}

    def test_label_wording(self):
        assert L.STATUS_LABELS["PENDING_APPROVAL"] == "รอเจ้าของเงินสดอนุมัติ"
        assert L.STATUS_LABELS["APPROVED"] == "อนุมัติจ่ายแล้ว รอส่งเคลียร์"
        assert L.STATUS_LABELS["CLEAR_PENDING"] == "รอหัวหน้าอนุมัติเคลียร์"
        assert L.STATUS_LABELS["SENT_BACK"] == "บัญชีตีกลับ รอแก้ไข"
        assert L.STATUS_LABELS["REVIEWED"] == "บัญชีตรวจผ่าน รอตั้งเบิก"
        assert L.STATUS_LABELS["CLOSED"] == "ปิดแล้ว"


class TestBatchTransitions:
    def test_round_path(self):
        assert L.assert_batch_transition("CLEAR_PENDING", "AWAITING_REVIEW") is None
        assert L.assert_batch_transition("AWAITING_REVIEW", "REVIEWED") is None
        assert L.assert_batch_transition("REVIEWED", "VOUCHERED") is None
        assert L.assert_batch_transition("VOUCHERED", "CLOSED") is None

    def test_head_can_reject_a_round(self):
        assert L.assert_batch_transition("CLEAR_PENDING", "CLEAR_REJECTED") is None

    def test_rejected_round_is_terminal(self):
        with pytest.raises(L.PettyRuleError) as exc:
            L.assert_batch_transition("CLEAR_REJECTED", "AWAITING_REVIEW")
        assert exc.value.http_status == 409

    def test_cannot_pay_before_voucher(self):
        with pytest.raises(L.PettyRuleError) as exc:
            L.assert_batch_transition("REVIEWED", "CLOSED")
        assert "ตรวจผ่าน รอตั้งเบิก" in str(exc.value)

    def test_labels_cover_every_round_status(self):
        assert set(L.BATCH_STATUS_LABELS) == set(L.BATCH_STATUSES)


class TestClearRequiredLevel:
    """หัวหน้าที่อนุมัติเคลียร์ = ผู้จัดการตามสายงาน: อย่างน้อยระดับ 5 และสูงกว่าเจ้าของเงินสด"""

    def test_staff_holder_needs_a_manager(self):
        assert L.clear_required_level(3) == 5

    def test_manager_holder_needs_the_next_level_up(self):
        assert L.clear_required_level(5) == 6

    def test_capped_at_ceo(self):
        assert L.clear_required_level(9) == 9

    def test_unknown_level_needs_a_manager(self):
        assert L.clear_required_level(None) == 5


class TestNormalizePlate:
    def test_strips_spaces_and_dashes(self):
        assert L.normalize_plate("71-4285") == L.normalize_plate("71 4285") == "714285"

    def test_strips_trailing_space(self):
        assert L.normalize_plate("71-4285 ") == "714285"

    def test_keeps_thai_letters(self):
        assert L.normalize_plate("70-ขก 1234") == "70ขก1234"

    def test_uppercases_latin(self):
        assert L.normalize_plate("f014") == "F014"

    def test_none_is_empty(self):
        assert L.normalize_plate(None) == ""


class TestTotals:
    def test_line_amount_rounds_half_up(self):
        assert L.line_amount(Decimal("1.5"), Decimal("33.333")) == Decimal("50.00")

    def test_real_voucher_total(self):
        items = (
            [{"qty": 1, "rate": 100}] * 12 + [{"qty": 1, "rate": 120}] * 3 + [{"qty": 1, "rate": 80}]
        )
        assert L.claim_total(items) == Decimal("1640.00")

    def test_total_equals_sum_of_rounded_lines(self):
        items = [{"qty": "1", "rate": "0.005"}] * 3
        assert L.claim_total(items) == sum(L.line_amount(i["qty"], i["rate"]) for i in items)

    def test_empty_is_zero(self):
        assert L.claim_total([]) == Decimal("0.00")


class TestPeriod:
    def test_min_max_of_event_dates(self):
        items = [{"event_date": date(2026, 9, 24)}, {"event_date": date(2026, 9, 17)}]
        assert L.period_of(items) == (date(2026, 9, 17), date(2026, 9, 24))

    def test_empty_is_none(self):
        assert L.period_of([]) == (None, None)


class TestDocNumbers:
    def test_form_code_per_branch_division(self):
        assert L.petty_form_code("03", "B02") == "PC-03-B02"

    def test_form_id_format(self):
        assert L.format_form_id("03", "B02", 2610, 1) == "LB-B02-2610-001"

    def test_form_id_grows_past_999(self):
        assert L.format_form_id("03", "B02", 2610, 1000) == "LB-B02-2610-1000"

    def test_form_id_matches_frontend_pattern(self):
        import re
        pattern = re.compile(r"^[A-Za-z0-9_-]+-\d{4}-\d{3,}$")  # lib/s3.ts FORM_ID_PATTERN
        assert pattern.match(L.format_form_id("03", "B02", 2610, 7))

    def test_batch_no_format(self):
        assert L.batch_no("03", "B02", 2610, 2) == "LB-B02-2610-B2"


class TestHardDupKey:
    def test_key_is_ref_plus_both_truck_fields(self):
        item = {"ref_no": "000055", "truck_code": "161", "truck_plate": "71-4285"}
        assert L.hard_dup_key(item) == ("000055", "161", "714285")

    def test_no_ref_no_means_no_key(self):
        assert L.hard_dup_key({"ref_no": "", "truck_code": "161", "truck_plate": "71-4285"}) is None
