from datetime import date

import pytest

from services.finance import petty_logic as L
from services.finance import petty_repo as repo


@pytest.fixture
def claim(db_session, fund, claim_payload, weigh_items, seq_stub, frozen_bkk):
    return repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")


class TestApprove:
    def test_sets_approver_and_time(self, db_session, claim):
        out = repo.set_status(db_session, claim, L.APPROVED, actor="12345", action="APPROVE",
                              fields={"approved_by": "12345"})
        assert out.status == "APPROVED" and out.approved_by == "12345" and out.approved_at is not None

    def test_second_approve_is_409(self, db_session, claim):
        repo.set_status(db_session, claim, L.APPROVED, actor="12345", action="APPROVE")
        with pytest.raises(L.PettyRuleError) as exc:
            repo.set_status(db_session, claim, L.APPROVED, actor="12345", action="APPROVE")
        assert exc.value.http_status == 409

    def test_logs_the_action(self, db_session, claim):
        repo.set_status(db_session, claim, L.APPROVED, actor="12345", action="APPROVE")
        from models.finance_petty_model import FinPettyLog
        actions = [l.action for l in db_session.query(FinPettyLog)
                   .filter(FinPettyLog.claim_id == claim.id).order_by(FinPettyLog.id).all()]
        assert actions == ["CREATE", "APPROVE"]


class TestReturnAndResubmit:
    def test_return_keeps_the_reason(self, db_session, claim):
        out = repo.set_status(db_session, claim, L.RETURNED, actor="12345", action="RETURN",
                              fields={"return_reason": "ใบชั่งไม่ครบ"}, remark="ใบชั่งไม่ครบ")
        assert out.status == "RETURNED" and out.return_reason == "ใบชั่งไม่ครบ"

    def test_returned_claim_can_be_edited_and_resubmitted(self, db_session, claim, weigh_items):
        repo.set_status(db_session, claim, L.RETURNED, actor="12345", action="RETURN")
        out = repo.update_draft(db_session, claim, {"payee_name": "อัญชัญ น่วมมี"}, weigh_items[:2],
                                actor="55555")
        assert out.amount == 200 and out.status == "PENDING_APPROVAL"

    def test_cannot_edit_after_approval(self, db_session, claim, weigh_items):
        repo.set_status(db_session, claim, L.APPROVED, actor="12345", action="APPROVE")
        with pytest.raises(L.PettyRuleError) as exc:
            repo.update_draft(db_session, claim, {}, weigh_items[:1], actor="55555")
        assert exc.value.http_status == 409

    def test_resubmit_is_logged_as_resubmit(self, db_session, claim, weigh_items):
        repo.set_status(db_session, claim, L.RETURNED, actor="12345", action="RETURN")
        repo.update_draft(db_session, claim, {}, weigh_items[:1], actor="55555")
        from models.finance_petty_model import FinPettyLog
        actions = [l.action for l in db_session.query(FinPettyLog)
                   .filter(FinPettyLog.claim_id == claim.id).order_by(FinPettyLog.id).all()]
        assert actions == ["CREATE", "RETURN", "RESUBMIT"]


class TestFullPath:
    """flow v2 (29/10/2026): อนุมัติ+จ่าย → รอบ → หัวหน้าอนุมัติเคลียร์ → บัญชีตรวจ → ทำจ่ายทั้งรอบ"""

    def test_every_step_ends_at_closed(self, db_session, fund, claim):
        repo.set_status(db_session, claim, L.APPROVED, actor="12345", action="APPROVE",
                        fields={"transfer_date": date(2026, 9, 25)})
        assert claim.approved_at is not None and claim.transfer_slip_at is not None
        repo.set_dd(db_session, claim, "LBDD26090931", actor="12345")
        batch = repo.create_round(db_session, fund.id, [claim.id], actor="12345", remark=None, required_level=5)
        repo.clear_approve_round(db_session, batch, actor="600100")
        repo.review_pass(db_session, claim, actor="80001", remark=None)
        db_session.refresh(batch)
        repo.voucher_round(db_session, batch, actor="80001", voucher_no="PV-1")
        repo.pay_round(db_session, batch, actor="90001", pay_date=date(2026, 10, 30))
        assert claim.status == "CLOSED" and claim.dd_no == "LBDD26090931"

    def test_timestamp_is_not_overwritten_on_the_second_pass(self, db_session, fund, claim, weigh_items):
        repo.set_status(db_session, claim, L.APPROVED, actor="12345", action="APPROVE")
        batch = repo.create_round(db_session, fund.id, [claim.id], actor="12345", remark=None, required_level=5)
        repo.clear_approve_round(db_session, batch, actor="600100")
        first = claim.submitted_to_acc_at
        repo.set_status(db_session, claim, L.SENT_BACK, actor="80001", action="SEND_BACK")
        repo.update_draft(db_session, claim, {}, weigh_items, actor="55555")
        assert claim.status == "AWAITING_REVIEW" and claim.submitted_to_acc_at == first
