from datetime import date
from decimal import Decimal

import pytest

from models.finance_model import FinAdvance
from services.finance import petty_logic as L
from services.finance import petty_repo as repo


def _paid_advance(db_session, form_id: str, amount: str, fin_status: str = "PAID",
                  employee_id: str = "12345"):
    """ใบ ADV ที่จ่ายแล้ว 1 ใบ — ต้องมี form_submissions คู่ด้วย เพราะ advance_outstanding
    ผูกเจ้าของยอดผ่าน form_submissions.created_by (fin_advances ไม่มีคอลัมน์ employee_id)"""
    from models.master_model import FormSubmission
    submission = FormSubmission(form_master_id=1, form_id=form_id, created_by=employee_id,
                                status="Open", status_approve="Approved")
    db_session.add(submission)
    db_session.flush()
    row = FinAdvance(submission_id=submission.id, form_id=form_id, amount_paid=Decimal(amount),
                     fin_status=fin_status)
    db_session.add(row)
    db_session.commit()
    return row


class TestOutstanding:
    def test_counts_paid_and_clearing_states(self, db_session):
        _paid_advance(db_session, "ADV-2609-001", "5000")
        _paid_advance(db_session, "ADV-2609-002", "3000", "CLEARING_SUBMITTED")
        _paid_advance(db_session, "ADV-2609-003", "1000", "SENT_BACK")
        assert repo.advance_outstanding(db_session, "12345") == Decimal("9000.00")

    def test_ignores_closed_advances(self, db_session):
        _paid_advance(db_session, "ADV-2609-004", "5000", "CLOSED")
        assert repo.advance_outstanding(db_session, "12345") == Decimal("0.00")

    def test_ignores_other_peoples_advances(self, db_session):
        _paid_advance(db_session, "ADV-2609-005", "5000", employee_id="99999")
        assert repo.advance_outstanding(db_session, "12345") == Decimal("0.00")

    def test_no_advance_is_zero_not_none(self, db_session):
        assert repo.advance_outstanding(db_session, "nobody") == Decimal("0.00")


class TestCommitted:
    def test_counts_from_approved_to_sent_back(self, db_session, fund, claim_payload, weigh_items,
                                               seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        assert repo.petty_committed(db_session, "12345") == Decimal("0.00")  # ยังรออนุมัติ
        repo.set_status(db_session, claim, L.APPROVED, "12345", "APPROVE")
        assert repo.petty_committed(db_session, "12345") == Decimal("1640.00")

    def test_closed_claim_releases_the_money(self, db_session, fund, claim_payload, weigh_items,
                                             seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        repo.set_status(db_session, claim, L.APPROVED, "12345", "APPROVE")
        batch = repo.create_round(db_session, fund.id, [claim.id], actor="12345", remark=None, required_level=5)
        repo.clear_approve_round(db_session, batch, actor="600100")
        repo.review_pass(db_session, claim, actor="80001", remark=None)
        assert repo.petty_committed(db_session, "12345") == Decimal("1640.00")  # ตรวจผ่านแล้วยังไม่จ่ายคืน
        db_session.refresh(batch)
        repo.voucher_round(db_session, batch, actor="80001", voucher_no="PV-1")
        repo.pay_round(db_session, batch, actor="90001", pay_date=date(2026, 10, 30))
        assert repo.petty_committed(db_session, "12345") == Decimal("0.00")

    def test_returned_claim_does_not_hold_money(self, db_session, fund, claim_payload, weigh_items,
                                                seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        repo.set_status(db_session, claim, L.RETURNED, "12345", "RETURN")
        assert repo.petty_committed(db_session, "12345") == Decimal("0.00")


class TestFundBalance:
    def test_available_is_outstanding_minus_committed(self, db_session, fund, claim_payload,
                                                      weigh_items, seq_stub, frozen_bkk):
        _paid_advance(db_session, "ADV-2609-010", "5000")
        claim = repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        repo.set_status(db_session, claim, L.APPROVED, "12345", "APPROVE")
        bal = repo.fund_balance(db_session, fund.id)
        assert bal["advance_outstanding"] == Decimal("5000.00")
        assert bal["petty_committed"] == Decimal("1640.00")
        assert bal["available"] == Decimal("3360.00")

    def test_available_can_go_negative(self, db_session, fund, claim_payload, weigh_items,
                                       seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        repo.set_status(db_session, claim, L.APPROVED, "12345", "APPROVE")
        assert repo.fund_balance(db_session, fund.id)["available"] == Decimal("-1640.00")

    def test_never_writes_to_fin_advances(self, db_session, fund, claim_payload, weigh_items,
                                          seq_stub, frozen_bkk):
        adv = _paid_advance(db_session, "ADV-2609-011", "5000")
        claim = repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        repo.set_status(db_session, claim, L.APPROVED, "12345", "APPROVE")
        repo.fund_balance(db_session, fund.id)
        db_session.refresh(adv)
        assert adv.amount_paid == Decimal("5000") and adv.fin_status == "PAID"

    def test_unknown_fund_raises(self, db_session, fund):
        with pytest.raises(LookupError):
            repo.fund_balance(db_session, fund.id + 99)
