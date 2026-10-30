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


# ---- คนอนุมัติกับคนโอนต้องเป็นคนละคน (ผู้ใช้สั่ง 30/10/2026) ----
# "ยานยนต์ sutiwat ต้องอนุมัติก่อน และเสถียรพงษ์จ่าย" — ถ้าหัวหน้าของผู้ขอเบิกคือเจ้าของเงินสด ข้ามไปอีกระดับ
from services.finance import petty_clear as clear  # noqa: E402


def _p(eid, uid, level, dept, division=20, site=2):
    return {"employee_id": eid, "id": uid, "level": level, "department_id": dept,
            "division_id": division, "site_id": site, "active": True}


@pytest.fixture
def auto_line():
    """แบบฝ่ายยานยนต์ในข้อมูลจริง: ผู้ขอเบิกแผนก 26 · เจ้าของเงินสดเป็นหัวหน้าแผนก 26 ระดับ 3
    · หัวหน้าแผนกอื่นในฝ่ายเดียวกันระดับ 3 · ผู้จัดการฝ่ายระดับ 5 อยู่แผนก 35"""
    return {p["employee_id"]: p for p in (
        _p("650520", 1, 1, 26), _p("690189", 2, 3, 26), _p("690410", 3, 3, 30), _p("670532", 4, 5, 35),
        _p("590373", 5, 1, 6, division=24), _p("690261", 6, 3, 6, division=24),
    )}


class TestApproverIsNotThePayer:
    def test_owner_who_is_the_head_is_skipped_one_level_up(self, auto_line):
        assert clear.head_rule(auto_line, {}, "650520", "690189") == (4, (2,))

    def test_owner_and_same_level_peer_cannot_approve_the_manager_can(self, auto_line):
        can = lambda who: clear.can_head(auto_line, {}, "650520", "690189", who)  # noqa: E731
        assert (can("690189"), can("690410"), can("670532")) == (False, False, True)

    def test_email_goes_to_the_manager(self, auto_line):
        assert [p["employee_id"] for p in clear.head_notify(auto_line, {}, "650520", "690189")] == ["670532"]

    def test_owner_outside_the_line_changes_nothing(self, auto_line):
        assert clear.head_rule(auto_line, {}, "590373", "690189") == (3, (2,))
        assert clear.can_head(auto_line, {}, "590373", "690189", "690261") is True
        assert [p["employee_id"] for p in clear.head_notify(auto_line, {}, "590373", "690189")] == ["690261"]

    def test_owner_opening_their_own_claim_goes_to_the_manager(self, auto_line):
        assert clear.can_head(auto_line, {}, "690189", "690189", "690189") is False
        assert [p["employee_id"] for p in clear.head_notify(auto_line, {}, "690189", "690189")] == ["670532"]
