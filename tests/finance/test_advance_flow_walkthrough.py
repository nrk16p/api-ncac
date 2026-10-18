"""Walk one Advance request through the whole flow and prove each step refuses the wrong moment.

รอตั้งเบิก → รอจ่าย → รอเคลียร์ → รอบัญชีตรวจ → ปิด, with the two detours
(บัญชีตีกลับก่อนจ่าย, ส่งกลับแก้ไขหลังเคลียร์).
"""
import os
from datetime import date

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

import pytest  # noqa: E402

from services.finance import advance_logic as L  # noqa: E402

OWNER = True
TRANSFER = date(2026, 10, 8)
CLEAR = date(2026, 10, 20)


def test_the_happy_path_walks_every_stage_in_order():
    # 1. approvals finished -> รอตั้งเบิก; accounting records the voucher
    L.check_voucher(L.AWAITING_VOUCHER, voucher_date=date(2026, 10, 9))

    # 2. รอจ่าย; finance pays and the clearing deadline is derived from the transfer date
    due = L.check_pay(L.AWAITING_PAYMENT, acc_active=True, amount_paid=5000,
                      transfer_date=TRANSFER, clear_due_date=None)
    assert due == L.default_due_date(TRANSFER)

    # 3. รอเคลียร์; the requester clears and owes money back
    settle, settle_date = L.check_clear(L.AWAITING_CLEARING, is_owner=OWNER, amount_paid=5000,
                                        clear_date=CLEAR, amount_actual=4500, settle_date=CLEAR)
    assert settle == 500
    assert settle_date == CLEAR

    # 4. รอบัญชีตรวจ; accounting closes it
    assert L.check_confirm(L.AWAITING_REVIEW, settle_amount=settle, settle_date=settle_date) is None


def test_clearing_that_spent_more_needs_a_top_up_date_to_close():
    settle, settle_date = L.check_clear(L.AWAITING_CLEARING, is_owner=OWNER, amount_paid=5000,
                                        clear_date=CLEAR, amount_actual=5450, settle_date=None)
    assert settle == -450
    assert settle_date is None
    with pytest.raises(L.AdvanceRuleError):
        L.check_confirm(L.AWAITING_REVIEW, settle_amount=settle, settle_date=None)
    assert L.check_confirm(L.AWAITING_REVIEW, settle_amount=settle, settle_date=CLEAR) == CLEAR


def test_money_left_over_cannot_be_cleared_without_a_return_date():
    with pytest.raises(L.AdvanceRuleError):
        L.check_clear(L.AWAITING_CLEARING, is_owner=OWNER, amount_paid=5000,
                      clear_date=CLEAR, amount_actual=4500, settle_date=None)


def test_spending_it_exactly_needs_no_return_date():
    settle, settle_date = L.check_clear(L.AWAITING_CLEARING, is_owner=OWNER, amount_paid=5000,
                                        clear_date=CLEAR, amount_actual=5000, settle_date=None)
    assert settle == 0
    assert settle_date is None


@pytest.mark.parametrize("status", [L.AWAITING_VOUCHER, L.RETURNED, L.AWAITING_REVIEW, L.SENT_BACK, L.CLOSED])
def test_paying_outside_the_two_payment_states_is_refused(status):
    with pytest.raises(L.InvalidTransition):
        L.check_pay(status, acc_active=True, amount_paid=1, transfer_date=TRANSFER, clear_due_date=None)


def test_paying_twice_is_refused_and_so_is_editing_a_payment_that_does_not_exist():
    with pytest.raises(L.InvalidTransition):  # already paid, this is a fresh pay
        L.check_pay(L.AWAITING_CLEARING, acc_active=True, amount_paid=1,
                    transfer_date=TRANSFER, clear_due_date=None, is_edit=False)
    with pytest.raises(L.InvalidTransition):  # not paid yet, this is an edit
        L.check_pay(L.AWAITING_PAYMENT, acc_active=True, amount_paid=1,
                    transfer_date=TRANSFER, clear_due_date=None, is_edit=True)


def test_a_clearing_deadline_cannot_fall_before_the_transfer():
    with pytest.raises(L.AdvanceRuleError):
        L.check_pay(L.AWAITING_PAYMENT, acc_active=True, amount_paid=1000,
                    transfer_date=TRANSFER, clear_due_date=date(2026, 10, 7))


def test_only_the_requester_clears_their_own_advance():
    with pytest.raises(L.NotAllowed):
        L.check_clear(L.AWAITING_CLEARING, is_owner=False, amount_paid=5000,
                      clear_date=CLEAR, amount_actual=5000, settle_date=None)


def test_a_submitted_clearing_is_locked_until_accounting_sends_it_back():
    with pytest.raises(L.InvalidTransition):
        L.check_clear(L.AWAITING_REVIEW, is_owner=OWNER, amount_paid=5000,
                      clear_date=CLEAR, amount_actual=5000, settle_date=None)
    # after being sent back the requester may edit again
    L.check_clear(L.SENT_BACK, is_owner=OWNER, amount_paid=5000,
                  clear_date=CLEAR, amount_actual=5000, settle_date=None)


def test_sending_a_clearing_back_demands_a_reason():
    with pytest.raises(L.AdvanceRuleError):
        L.check_send_back(L.AWAITING_REVIEW, review_remark="   ")
    L.check_send_back(L.AWAITING_REVIEW, review_remark="ใบเสร็จไม่ครบ")


def test_accounting_returns_before_the_voucher_and_the_requester_sends_it_again():
    with pytest.raises(L.AdvanceRuleError):
        L.check_return(L.AWAITING_VOUCHER, remark="")
    L.check_return(L.AWAITING_VOUCHER, remark="แนบเอกสารไม่ครบ")
    with pytest.raises(L.NotAllowed):
        L.check_resubmit(L.RETURNED, is_owner=False)
    L.check_resubmit(L.RETURNED, is_owner=OWNER)


def test_a_stale_clearing_is_refused_so_two_reviewers_cannot_collide():
    L.check_fresh(expected=7, actual=7)
    with pytest.raises(L.InvalidTransition):
        L.check_fresh(expected=7, actual=9)


def test_the_overdue_flag_follows_the_clearing_deadline():
    assert L.derive_status("Approved", L.FIN_PAID, date(2026, 10, 20), date(2026, 10, 19))[1] is False
    assert L.derive_status("Approved", L.FIN_PAID, date(2026, 10, 20), date(2026, 10, 21))[1] is True
