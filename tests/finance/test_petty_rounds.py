"""รอบส่งเคลียร์ (flow v2 29/10/2026)

เจ้าของเงินสดรวมใบที่อนุมัติจ่ายแล้วเป็นรอบ → หัวหน้าอนุมัติเคลียร์ทั้งรอบ → บัญชีตรวจทีละใบ
→ ตรวจผ่านครบทั้งรอบ → บัญชีตั้งเบิก → การเงินทำจ่าย → ปิดทั้งรอบ
"""
from datetime import date
from decimal import Decimal

import pytest

from models.finance_petty_model import FinPettyFund, FinPettyLog
from services.finance import petty_logic as L
from services.finance import petty_repo as repo


@pytest.fixture
def approved(db_session, fund, claim_payload, weigh_items, seq_stub, frozen_bkk):
    """ใบที่เจ้าของเงินสด (12345) อนุมัติจ่ายแล้ว — สร้างใหม่ทุกครั้งที่เรียก"""
    def _make(items=None):
        claim = repo.create_claim(db_session, claim_payload(), items or weigh_items, actor="55555")
        return repo.set_status(db_session, claim, L.APPROVED, "12345", "APPROVE",
                               fields={"approved_by": "12345", "transfer_date": date(2026, 9, 25)})
    return _make


def _round(db_session, fund, claims, required=5):
    return repo.create_round(db_session, fund.id, [c.id for c in claims], actor="12345", remark=None,
                             required_level=required)


def _actions(db_session, claim):
    return [row.action for row in db_session.query(FinPettyLog)
            .filter(FinPettyLog.claim_id == claim.id).order_by(FinPettyLog.id).all()]


class TestCreateRound:
    def test_puts_every_claim_into_the_round(self, db_session, fund, approved):
        a, b = approved(), approved()
        batch = _round(db_session, fund, [a, b])
        assert batch.status == "CLEAR_PENDING" and batch.total_amount == Decimal("3280.00")
        assert batch.submitted_by == "12345" and batch.submitted_at is not None
        assert batch.clear_required_level == 5 and batch.closed_at is None
        assert {a.status, b.status} == {"CLEAR_PENDING"} and a.batch_id == b.batch_id == batch.id
        assert _actions(db_session, a)[-1] == "SUBMIT_ROUND"

    def test_round_number_runs_per_fund_and_month(self, db_session, fund, approved):
        first = _round(db_session, fund, [approved()])
        second = _round(db_session, fund, [approved()])
        assert first.batch_no == "LB-B02-2610-B1" and second.batch_no == "LB-B02-2610-B2"

    def test_claim_not_yet_approved_is_rejected(self, db_session, fund, claim_payload, weigh_items,
                                                seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        with pytest.raises(L.PettyRuleError) as exc:
            _round(db_session, fund, [claim])
        assert exc.value.http_status == 409 and claim.form_id in str(exc.value)

    def test_claim_already_in_a_round_is_rejected(self, db_session, fund, approved):
        a = approved()
        _round(db_session, fund, [a])
        with pytest.raises(L.PettyRuleError):
            _round(db_session, fund, [a])

    def test_claim_from_another_fund_is_rejected(self, db_session, fund, approved):
        other = FinPettyFund(branch_code="01", division_code="A01", holder_employee_id="22222")
        db_session.add(other)
        db_session.commit()
        a = approved()
        with pytest.raises(L.PettyRuleError) as exc:
            repo.create_round(db_session, other.id, [a.id], actor="22222", remark=None, required_level=5)
        assert exc.value.http_status == 409

    def test_missing_claim_id_is_404(self, db_session, fund, approved):
        with pytest.raises(L.PettyRuleError) as exc:
            repo.create_round(db_session, fund.id, [approved().id, 9999], actor="12345", remark=None,
                              required_level=5)
        assert exc.value.http_status == 404

    def test_unknown_fund_raises(self, db_session, fund, approved):
        with pytest.raises(LookupError):
            repo.create_round(db_session, fund.id + 99, [approved().id], actor="12345", remark=None,
                              required_level=5)


class TestHeadDecision:
    def test_approve_sends_the_whole_round_to_accounting(self, db_session, fund, approved):
        a, b = approved(), approved()
        batch = _round(db_session, fund, [a, b])
        out = repo.clear_approve_round(db_session, batch, actor="600100")
        assert out.status == "AWAITING_REVIEW" and out.clear_approved_by == "600100"
        assert out.clear_approved_at is not None
        assert {a.status, b.status} == {"AWAITING_REVIEW"} and a.submitted_to_acc_at is not None
        assert _actions(db_session, a)[-1] == "CLEAR_APPROVE"

    def test_cannot_approve_twice(self, db_session, fund, approved):
        batch = _round(db_session, fund, [approved()])
        repo.clear_approve_round(db_session, batch, actor="600100")
        with pytest.raises(L.PettyRuleError) as exc:
            repo.clear_approve_round(db_session, batch, actor="600100")
        assert exc.value.http_status == 409

    def test_reject_hands_the_claims_back_to_the_holder(self, db_session, fund, approved):
        a = approved()
        batch = _round(db_session, fund, [a])
        out = repo.clear_reject_round(db_session, batch, actor="600100", reason="สลิปไม่ชัด")
        assert out.status == "CLEAR_REJECTED" and out.clear_reject_reason == "สลิปไม่ชัด"
        assert a.status == "APPROVED" and a.batch_id is None
        assert _actions(db_session, a)[-1] == "CLEAR_REJECT"

    def test_rejected_claims_can_go_into_a_new_round(self, db_session, fund, approved):
        a = approved()
        repo.clear_reject_round(db_session, _round(db_session, fund, [a]), actor="600100", reason="x")
        again = _round(db_session, fund, [a])
        assert again.batch_no.endswith("-B2") and a.batch_id == again.id

    def test_rejected_claims_still_hold_the_money(self, db_session, fund, approved):
        a = approved()
        repo.clear_reject_round(db_session, _round(db_session, fund, [a]), actor="600100", reason="x")
        assert repo.petty_committed(db_session, "12345") == Decimal("1640.00")


class TestAccountingReview:
    def _in_review(self, db_session, fund, approved, n=2):
        claims = [approved() for _ in range(n)]
        batch = repo.clear_approve_round(db_session, _round(db_session, fund, claims), actor="600100")
        return batch, claims

    def test_round_waits_until_every_claim_passes(self, db_session, fund, approved):
        batch, (a, b) = self._in_review(db_session, fund, approved)
        repo.review_pass(db_session, a, actor="80001", remark=None)
        assert a.status == "REVIEWED" and a.reviewed_by == "80001"
        db_session.refresh(batch)
        assert batch.status == "AWAITING_REVIEW"
        repo.review_pass(db_session, b, actor="80001", remark="ครบ")
        db_session.refresh(batch)
        assert batch.status == "REVIEWED"

    def test_sent_back_claim_stays_in_the_round(self, db_session, fund, approved):
        batch, (a, b) = self._in_review(db_session, fund, approved)
        repo.set_status(db_session, a, L.SENT_BACK, "80001", "SEND_BACK", fields={"review_remark": "บิลเบลอ"})
        repo.review_pass(db_session, b, actor="80001", remark=None)
        db_session.refresh(batch)
        assert a.batch_id == batch.id and batch.status == "AWAITING_REVIEW"

    def test_clerk_fixes_a_sent_back_claim_and_it_goes_straight_back_to_accounting(
            self, db_session, fund, approved, weigh_items):
        batch, (a, b) = self._in_review(db_session, fund, approved)
        repo.set_status(db_session, a, L.SENT_BACK, "80001", "SEND_BACK", fields={"review_remark": "บิลเบลอ"})
        out = repo.update_draft(db_session, a, {}, weigh_items[:3], actor="55555")
        assert out.status == "AWAITING_REVIEW" and out.amount == Decimal("300.00")
        assert _actions(db_session, a)[-1] == "RESUBMIT_ACC"
        repo.review_pass(db_session, a, actor="80001", remark=None)
        repo.review_pass(db_session, b, actor="80001", remark=None)
        db_session.refresh(batch)
        assert batch.status == "REVIEWED"

    def test_cannot_pass_a_claim_that_is_not_awaiting_review(self, db_session, fund, approved):
        with pytest.raises(L.PettyRuleError) as exc:
            repo.review_pass(db_session, approved(), actor="80001", remark=None)
        assert exc.value.http_status == 409


class TestVoucherAndPay:
    def _reviewed_round(self, db_session, fund, approved):
        claims = [approved(), approved()]
        batch = repo.clear_approve_round(db_session, _round(db_session, fund, claims), actor="600100")
        for c in claims:
            repo.review_pass(db_session, c, actor="80001", remark=None)
        db_session.refresh(batch)
        return batch, claims

    def test_voucher_then_pay_closes_the_round_and_releases_the_money(self, db_session, fund, approved):
        batch, (a, b) = self._reviewed_round(db_session, fund, approved)
        assert repo.petty_committed(db_session, "12345") == Decimal("3280.00")
        repo.voucher_round(db_session, batch, actor="80001", voucher_no="PV-2610-001")
        assert batch.status == "VOUCHERED" and batch.voucher_no == "PV-2610-001"
        out = repo.pay_round(db_session, batch, actor="90001", pay_date=date(2026, 10, 30))
        assert out.status == "CLOSED" and out.pay_date == date(2026, 10, 30)
        assert out.paid_by == out.closed_by == "90001" and out.closed_at is not None
        assert {a.status, b.status} == {"CLOSED"}
        assert repo.petty_committed(db_session, "12345") == Decimal("0.00")
        assert _actions(db_session, a)[-1] == "PAY_CLOSE"

    def test_voucher_recounts_the_total(self, db_session, fund, approved, weigh_items):
        claims = [approved(), approved()]
        batch = repo.clear_approve_round(db_session, _round(db_session, fund, claims), actor="600100")
        repo.set_status(db_session, claims[0], L.SENT_BACK, "80001", "SEND_BACK")
        repo.update_draft(db_session, claims[0], {}, weigh_items[:1], actor="55555")
        for c in claims:
            repo.review_pass(db_session, c, actor="80001", remark=None)
        db_session.refresh(batch)
        repo.voucher_round(db_session, batch, actor="80001", voucher_no="PV-1")
        assert batch.total_amount == Decimal("1740.00")

    def test_cannot_voucher_before_every_claim_passes(self, db_session, fund, approved):
        claims = [approved()]
        batch = repo.clear_approve_round(db_session, _round(db_session, fund, claims), actor="600100")
        with pytest.raises(L.PettyRuleError) as exc:
            repo.voucher_round(db_session, batch, actor="80001", voucher_no="PV-1")
        assert exc.value.http_status == 409

    def test_cannot_pay_before_voucher(self, db_session, fund, approved):
        batch, _ = self._reviewed_round(db_session, fund, approved)
        with pytest.raises(L.PettyRuleError):
            repo.pay_round(db_session, batch, actor="90001", pay_date=date(2026, 10, 30))


class TestDdNumber:
    def test_holder_adds_the_dd_while_approved(self, db_session, approved):
        a = approved()
        out = repo.set_dd(db_session, a, "LBDD26090931", actor="12345")
        assert out.dd_no == "LBDD26090931" and _actions(db_session, a)[-1] == "SET_DD"

    def test_not_after_the_round_is_sent(self, db_session, fund, approved):
        a = approved()
        _round(db_session, fund, [a])
        with pytest.raises(L.PettyRuleError) as exc:
            repo.set_dd(db_session, a, "LBDD1", actor="12345")
        assert exc.value.http_status == 409
