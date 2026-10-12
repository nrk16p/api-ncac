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
        repo.set_status(db_session, claim, L.TRANSFERRED, "12345", "TRANSFER")
        repo.set_status(db_session, claim, L.AWAITING_REVIEW, "12345", "SUBMIT_ACC")
        repo.set_status(db_session, claim, L.CLOSED, "90001", "CLOSE")
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


class TestCloseBatch:
    def _ready_claim(self, db_session, claim_payload, weigh_items):
        claim = repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        repo.set_status(db_session, claim, L.APPROVED, "12345", "APPROVE")
        repo.set_status(db_session, claim, L.TRANSFERRED, "12345", "TRANSFER")
        repo.set_status(db_session, claim, L.AWAITING_REVIEW, "12345", "SUBMIT_ACC")
        return claim

    def test_closes_every_claim_and_sums_the_total(self, db_session, fund, claim_payload,
                                                   weigh_items, seq_stub, frozen_bkk):
        a = self._ready_claim(db_session, claim_payload, weigh_items)
        b = self._ready_claim(db_session, claim_payload, weigh_items)
        batch = repo.close_batch(db_session, fund.id, [a.id, b.id], actor="90001", remark=None)
        assert batch.total_amount == Decimal("3280.00")
        assert {a.status, b.status} == {"CLOSED"}
        assert a.batch_id == batch.id

    def test_batch_no_is_sequential_per_fund_and_month(self, db_session, fund, claim_payload,
                                                       weigh_items, seq_stub, frozen_bkk):
        a = self._ready_claim(db_session, claim_payload, weigh_items)
        first = repo.close_batch(db_session, fund.id, [a.id], actor="90001", remark=None)
        b = self._ready_claim(db_session, claim_payload, weigh_items)
        second = repo.close_batch(db_session, fund.id, [b.id], actor="90001", remark=None)
        assert first.batch_no.endswith("-B1") and second.batch_no.endswith("-B2")

    def test_claim_not_in_awaiting_review_is_rejected(self, db_session, fund, claim_payload,
                                                      weigh_items, seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        with pytest.raises(L.PettyRuleError) as exc:
            repo.close_batch(db_session, fund.id, [claim.id], actor="90001", remark=None)
        assert exc.value.http_status == 409

    def test_claim_from_another_fund_is_rejected(self, db_session, fund, claim_payload,
                                                weigh_items, seq_stub, frozen_bkk):
        from models.finance_petty_model import FinPettyFund
        other = FinPettyFund(branch_code="01", division_code="A01", holder_employee_id="22222")
        db_session.add(other)
        db_session.commit()
        claim = self._ready_claim(db_session, claim_payload, weigh_items)
        with pytest.raises(L.PettyRuleError):
            repo.close_batch(db_session, other.id, [claim.id], actor="90001", remark=None)

    def test_missing_claim_id_is_rejected(self, db_session, fund, claim_payload, weigh_items,
                                          seq_stub, frozen_bkk):
        a = self._ready_claim(db_session, claim_payload, weigh_items)
        with pytest.raises(L.PettyRuleError) as exc:
            repo.close_batch(db_session, fund.id, [a.id, 9999], actor="90001", remark=None)
        assert exc.value.http_status == 404
