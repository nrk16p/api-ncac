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
    def test_seven_steps_end_at_closed(self, db_session, claim):
        repo.set_status(db_session, claim, L.APPROVED, actor="12345", action="APPROVE")
        repo.set_status(db_session, claim, L.TRANSFERRED, actor="12345", action="TRANSFER",
                        fields={"transfer_date": date(2026, 9, 25)})
        repo.set_status(db_session, claim, L.AWAITING_REVIEW, actor="12345", action="SUBMIT_ACC",
                        fields={"dd_no": "LBDD26090931"})
        repo.set_status(db_session, claim, L.CLOSED, actor="90001", action="CLOSE")
        assert claim.status == "CLOSED" and claim.dd_no == "LBDD26090931"

    def test_accounting_can_send_back_then_pass(self, db_session, claim):
        repo.set_status(db_session, claim, L.APPROVED, actor="12345", action="APPROVE")
        repo.set_status(db_session, claim, L.TRANSFERRED, actor="12345", action="TRANSFER")
        repo.set_status(db_session, claim, L.AWAITING_REVIEW, actor="12345", action="SUBMIT_ACC")
        repo.set_status(db_session, claim, L.SENT_BACK, actor="80001", action="SEND_BACK",
                        fields={"review_remark": "ใบชั่งเบลอ"})
        out = repo.set_status(db_session, claim, L.AWAITING_REVIEW, actor="12345", action="SUBMIT_ACC")
        assert out.status == "AWAITING_REVIEW" and out.review_remark == "ใบชั่งเบลอ"

    def test_timestamp_is_not_overwritten_on_the_second_pass(self, db_session, claim):
        repo.set_status(db_session, claim, L.APPROVED, actor="12345", action="APPROVE")
        repo.set_status(db_session, claim, L.TRANSFERRED, actor="12345", action="TRANSFER")
        repo.set_status(db_session, claim, L.AWAITING_REVIEW, actor="12345", action="SUBMIT_ACC")
        first = claim.submitted_to_acc_at
        repo.set_status(db_session, claim, L.SENT_BACK, actor="80001", action="SEND_BACK")
        repo.set_status(db_session, claim, L.AWAITING_REVIEW, actor="12345", action="SUBMIT_ACC")
        assert claim.submitted_to_acc_at == first
