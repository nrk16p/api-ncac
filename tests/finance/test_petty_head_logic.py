"""ขั้นหัวหน้าอนุมัติก่อนโอน (ผู้ใช้สั่ง 30/10/2026): ผู้ขอเบิกส่ง → หัวหน้าอนุมัติ → เจ้าของเงินสดอนุมัติพร้อมโอน"""
import pytest

from services.finance import petty_logic as L


class TestHeadStatus:
    def test_a_new_status_waits_for_the_head(self):
        assert L.PENDING_HEAD == "PENDING_HEAD"
        assert L.STATUS_LABELS["PENDING_HEAD"] == "รอหัวหน้าอนุมัติ"
        assert L.STATUSES[0] == "PENDING_HEAD"

    def test_head_approves_into_the_cash_owner_queue(self):
        assert L.assert_transition("PENDING_HEAD", "PENDING_APPROVAL") is None

    def test_head_can_return_to_the_requester(self):
        assert L.assert_transition("PENDING_HEAD", "RETURNED") is None

    def test_money_cannot_skip_the_head(self):
        with pytest.raises(L.PettyRuleError) as exc:
            L.assert_transition("PENDING_HEAD", "APPROVED")
        assert exc.value.http_status == 409 and "รอหัวหน้าอนุมัติ" in str(exc.value)

    def test_a_returned_claim_goes_back_to_the_head(self):
        assert L.assert_transition("RETURNED", "PENDING_HEAD") is None
        with pytest.raises(L.PettyRuleError):
            L.assert_transition("RETURNED", "PENDING_APPROVAL")


class TestHeadLevel:
    @pytest.mark.parametrize("requester, need", [(1, 3), (2, 3), (3, 4), (5, 6), (9, 9), (None, 3)])
    def test_head_is_at_least_a_supervisor_and_above_the_requester(self, requester, need):
        assert L.head_required_level(requester) == need
