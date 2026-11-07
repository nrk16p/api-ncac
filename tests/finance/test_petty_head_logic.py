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

    def test_a_returned_claim_restarts_at_the_head_or_the_cash_owner(self):
        """ส่งใหม่หลังตีกลับ: ปกติกลับไปรอหัวหน้า · ถ้า TOA ให้เจ้าของเงินสดอนุมัติเองได้ ไปรอเจ้าของเงินสดเลย (7/11/2026)"""
        assert L.assert_transition("RETURNED", "PENDING_HEAD") is None
        assert L.assert_transition("RETURNED", "PENDING_APPROVAL") is None


class TestHeadLevel:
    @pytest.mark.parametrize("requester, need", [(1, 3), (2, 3), (3, 4), (5, 6), (9, 9), (None, 3)])
    def test_head_is_at_least_a_supervisor_and_above_the_requester(self, requester, need):
        assert L.head_required_level(requester) == need


# ---- ขั้นหัวหน้าตาม TOA (ผู้ใช้สั่ง 7/11/2026 "ถ้า TOA ถูก ไม่ต้องผ่านหัวหน้าอนุมัติ → เจ้าของเงินสดทีเดียว") ----
# ยกเลิกกติกา "คนอนุมัติ ≠ คนโอน" ของ 30/10: เจ้าของเงินสดที่ TOA ให้อำนาจ อนุมัติ+โอนเองได้เลย
from services.finance import petty_clear as clear  # noqa: E402

TOA = [  # ชุดเดียวกับ prod 7/11/2026 (fin_approval_tiers)
    {"clause": "6.7", "amount_max": 2000, "min_level": 2, "approver_label": "Asst. Sup", "sort_order": 1},
    {"clause": "6.6", "amount_max": 5000, "min_level": 3, "approver_label": "Sup", "sort_order": 2},
    {"clause": "6.5", "amount_max": 20000, "min_level": 5, "approver_label": "MGR", "sort_order": 3},
    {"clause": "6.1", "amount_max": None, "min_level": 9, "approver_label": "ExC", "sort_order": 7},
]


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


class TestToaLevel:
    @pytest.mark.parametrize("amount, requester, need", [
        (500, 1, 2), (2000, 1, 2), (2000, 2, 3), (2000.01, 1, 3), (5000, 1, 3), (6000, 1, 5), (6000, 5, 6), (99999999, 1, 9)])
    def test_level_comes_from_the_toa_tier_and_is_above_the_requester(self, amount, requester, need):
        assert clear.toa_required_level(TOA, requester, amount) == need

    def test_without_a_toa_table_the_old_v3_formula_applies(self):
        assert clear.toa_required_level([], 1, 500) == 3
        assert clear.toa_required_level(None, 4, 500) == 5


class TestCashOwnerCoveredByToa:
    def test_owner_in_the_line_with_enough_level_skips_the_head(self, auto_line):
        assert clear.holder_covers(auto_line, {}, TOA, "650520", "690189", 1800) is True

    def test_owner_without_enough_level_for_the_amount_needs_a_head(self, auto_line):
        # 6,000 บาท → ระดับ 5 · เจ้าของเงินสดระดับ 3 ไม่ถึง
        assert clear.holder_covers(auto_line, {}, TOA, "650520", "690189", 6000) is False

    def test_owner_outside_the_requesters_line_needs_a_head(self, auto_line):
        assert clear.holder_covers(auto_line, {}, TOA, "590373", "690189", 500) is False

    def test_owner_opening_their_own_claim_is_not_covered(self, auto_line):
        assert clear.holder_covers(auto_line, {}, TOA, "690189", "690189", 500) is False

    def test_unknown_people_are_not_covered(self, auto_line):
        assert clear.holder_covers(auto_line, {}, TOA, "nobody", "690189", 500) is False
        assert clear.holder_covers(auto_line, {}, TOA, "650520", None, 500) is False


class TestHeadStepWhenNeeded:
    def test_owner_is_no_longer_excluded_from_the_head_step(self, auto_line):
        assert clear.head_rule(auto_line, {}, "650520", "690189", TOA, 1800) == (2, ())

    def test_head_level_follows_the_amount(self, auto_line):
        can = lambda who, amt: clear.can_head(auto_line, {}, "650520", "690189", who, TOA, amt)  # noqa: E731
        # 1,800: ระดับ 2 ขึ้นไป → เจ้าของเงินสด (3 แผนกเดียวกัน) กดได้ · คนแผนกอื่นในฝ่ายไม่ได้ เพราะในแผนกมีคนกดได้แล้ว
        # (กติกาแผนกก่อนของ Advance: ผจก.ระดับ 5 ต่างแผนกก็ไม่ได้ ถ้าไม่ได้เป็นผู้ดูแลฝ่าย)
        assert (can("690189", 1800), can("690410", 1800), can("670532", 1800)) == (True, False, False)
        # 6,000: ระดับ 5 → มีแค่ผู้จัดการฝ่าย
        assert (can("690189", 6000), can("690410", 6000), can("670532", 6000)) == (False, False, True)

    def test_email_goes_to_the_lowest_eligible_level(self, auto_line):
        assert [p["employee_id"] for p in clear.head_notify(auto_line, {}, "650520", "690189", TOA, 6000)] == ["670532"]
        assert [p["employee_id"] for p in clear.head_notify(auto_line, {}, "590373", "690189", TOA, 1000)] == ["690261"]

    def test_owner_opening_their_own_claim_goes_to_the_manager(self, auto_line):
        assert clear.can_head(auto_line, {}, "690189", "690189", "690189", TOA, 1000) is False
        assert [p["employee_id"] for p in clear.head_notify(auto_line, {}, "690189", "690189", TOA, 1000)] == ["670532"]


class TestClaimCap:
    def test_two_thousand_is_fine_and_a_satang_more_is_not(self):
        assert L.assert_claim_cap(2000) is None
        with pytest.raises(L.PettyRuleError) as exc:
            L.assert_claim_cap("2000.01")
        assert exc.value.http_status == 422 and "2,000" in str(exc.value) and "Advance" in str(exc.value)


class TestWeighSingleDriverRole:
    def test_mixed_roles_in_one_weigh_claim_are_rejected(self):
        rows = [{"driver_role": "พจส."}, {"driver_role": "พจร."}]
        with pytest.raises(L.PettyRuleError):
            L.assert_single_driver_role("WEIGH", rows)

    def test_one_role_or_blank_rows_pass_and_other_types_are_ignored(self):
        assert L.assert_single_driver_role("WEIGH", [{"driver_role": "พจส."}, {"driver_role": ""}, {}]) is None
        assert L.assert_single_driver_role("TOLL", [{"driver_role": "พจส."}, {"driver_role": "พจร."}]) is None


class TestLockedFund:
    class F:
        def __init__(self, id, branch, division, active=True):
            self.id, self.branch_code, self.division_code, self.is_active = id, branch, division, active

    funds = [F(1, "03", "B02"), F(12, "03", "A04"), F(14, "03", "B04"), F(10, "01", "A04"), F(99, "03", "B01", active=False)]

    def test_own_department_fund_first(self):
        assert L.locked_fund(self.funds, "3B02", "TOLL").id == 1

    def test_accounting_fund_of_the_branch_when_the_department_has_none(self):
        assert L.locked_fund(self.funds, "3B01", "TOLL").id == 12  # B01 ปิดอยู่ → บัญชี

    def test_none_when_the_branch_has_neither(self):
        assert L.locked_fund(self.funds, "2B02", "TOLL") is None

    def test_type_restriction_still_applies(self):
        assert L.locked_fund(self.funds, "3B04", "TIRE") is None  # ค่ายาง = B02 เท่านั้น บัญชีมาแทนไม่ได้
        assert L.locked_fund(self.funds, "3B04", "WEIGH").id == 14

    def test_bad_code_is_none(self):
        assert L.locked_fund(self.funds, "", "TOLL") is None and L.locked_fund(self.funds, "x", "TOLL") is None


class TestExpenseCode:
    """รหัสใบสำคัญจ่าย (ข้อ 8): พจร. → x500 · พจส. โม่ → 3200/2100 · พจส. Trailer/Side curtain → 3300/2100"""
    @staticmethod
    def f(cost_center, items):
        from services.finance.petty_cost_centers import expense_code_for
        return expense_code_for(cost_center, items)

    def test_pjr_anywhere_is_subcontractor_code(self):
        assert self.f("3B02", [{"driver_role": "พจส.", "fleet": "Mixer L"}, {"driver_role": "พจร.", "fleet": "Mixer L"}]) == "3500"
        assert self.f("2B01", [{"driver_role": "พจร."}]) == "2500"

    def test_mixer_company_driver(self):
        assert self.f("3B02", [{"driver_role": "พจส.", "fleet": "Mixer S"}]) == "3200"
        assert self.f("3B02", [{"driver_role": "พจส."}]) == "3200"  # ไม่รู้ฟลีท แต่เป็น พจส. → บริการ
        assert self.f("2B02", [{"driver_role": "พจส.", "fleet": "Mixer L"}]) == "2100"

    def test_trailer_and_side_curtain(self):
        assert self.f("3B02", [{"fleet": "Trailer"}]) == "3300"
        assert self.f("3B02", [{"driver_role": "พจส.", "fleet": "Side curtain"}]) == "3300"
        assert self.f("2B02", [{"fleet": "Trailer"}]) == "2100"

    def test_unknown(self):
        assert self.f("1B06", [{"driver_role": "พจส."}]) is None
        assert self.f("3B02", [{"ref_no": "x"}]) is None
        assert self.f(None, [{"driver_role": "พจส."}]) is None
