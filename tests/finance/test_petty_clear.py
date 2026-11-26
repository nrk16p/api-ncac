"""หัวหน้าที่อนุมัติเคลียร์ตามสายงาน (flow v2 29/10/2026)

ผู้ใช้: "ยานยนต์ ลาดกระบัง ก็ให้ผู้จัดการอนุมัติต่อ ตามสายงาน" — ใช้ engine เดียวกับ Advance
โดยให้เจ้าของเงินสดเป็น "ผู้ขอ" ของขั้นนี้
"""
from services.finance import petty_clear as C

YAN_LKB = 30      # แผนกยานยนต์ ลาดกระบัง
YAN_DIV = 7       # ฝ่ายยานยนต์
OTHER_DEPT = 31   # อีกแผนกในฝ่ายเดียวกัน
LKB, SRB = 3, 2   # site


def person(eid, uid, level, dept=YAN_LKB, division=YAN_DIV, site=LKB, active=True):
    return {"employee_id": eid, "id": uid, "level": level, "department_id": dept,
            "division_id": division, "site_id": site, "active": active}


HOLDER = person("690189", 1, 4)
MANAGER = person("600100", 2, 5)
SENIOR = person("590100", 3, 7)
PEER = person("690190", 4, 4)
OUTSIDER = person("600200", 5, 6, dept=99, division=99)
CEO = person("360002", 6, 9, dept=1, division=1, site=1)


def people(*rows):
    return {p["employee_id"]: p for p in rows}


class TestCanClear:
    def test_manager_in_the_same_department_may_approve(self):
        assert C.can_clear(people(HOLDER, MANAGER), {}, "690189", 5, "600100")

    def test_higher_level_in_the_line_may_approve_too(self):
        assert C.can_clear(people(HOLDER, MANAGER, SENIOR), {}, "690189", 5, "590100")

    def test_peer_below_the_required_level_may_not(self):
        assert not C.can_clear(people(HOLDER, MANAGER, PEER), {}, "690189", 5, "690190")

    def test_holder_never_approves_their_own_round(self):
        boss_holder = person("600100", 2, 6)
        assert not C.can_clear(people(boss_holder), {}, "600100", 7, "600100")

    def test_another_department_may_not(self):
        assert not C.can_clear(people(HOLDER, MANAGER, OUTSIDER), {}, "690189", 5, "600200")

    def test_division_overseer_may_approve(self):
        assert C.can_clear(people(HOLDER, MANAGER, OUTSIDER), {"600200": {YAN_DIV}}, "690189", 5, "600200")

    def test_ceo_may_approve_anything(self):
        assert C.can_clear(people(HOLDER, MANAGER, CEO), {}, "690189", 5, "360002")

    def test_falls_back_to_the_division_when_the_department_has_no_manager(self):
        other_manager = person("600300", 7, 5, dept=OTHER_DEPT)
        assert C.can_clear(people(HOLDER, other_manager), {}, "690189", 5, "600300")

    def test_unknown_holder_or_approver_is_refused(self):
        assert not C.can_clear(people(MANAGER), {}, "690189", 5, "600100")
        assert not C.can_clear(people(HOLDER), {}, "690189", 5, "600100")

    def test_inactive_manager_may_not(self):
        gone = person("600100", 2, 5, active=False)
        assert not C.can_clear(people(HOLDER, gone), {}, "690189", 5, "600100")


class TestWhoGetsTheEmail:
    def test_only_the_lowest_eligible_level(self):
        to = C.clear_notify(people(HOLDER, MANAGER, SENIOR, CEO), {}, "690189", 5)
        assert [p["employee_id"] for p in to] == ["600100"]

    def test_same_site_first(self):
        srb_manager = person("600400", 8, 5, site=SRB)
        to = C.clear_notify(people(HOLDER, MANAGER, srb_manager), {}, "690189", 5)
        assert [p["employee_id"] for p in to] == ["600100"]

    def test_nobody_when_the_holder_is_unknown(self):
        assert C.clear_notify(people(MANAGER), {}, "690189", 5) == []


class _Fund:
    def __init__(self, branch, division, holder):
        self.branch_code, self.division_code, self.holder_employee_id = branch, division, holder


class TestNamedClearApprovers:
    """26/11/2026 บัญชีลาดกระบัง (03-A04 ฟาง) ส่งเคลียร์ให้แค่นุ่นกับพี่ปูอนุมัติ · นุ่นได้อีเมล"""
    FANG = person("610055", 10, 2, dept=6, division=8, site=LKB)
    NUN = person("690261", 11, 3, dept=6, division=8, site=LKB)
    POO = person("600181", 12, 3, dept=6, division=8, site=SRB)
    MANAGER = person("690604", 13, 5, dept=6, division=8, site=1)
    LKB_ACC = _Fund("03", "A04", "610055")

    def ppl(self):
        return people(self.FANG, self.NUN, self.POO, self.MANAGER, CEO)

    def test_the_lat_krabang_accounting_fund_is_named(self):
        assert C.clear_named(self.LKB_ACC) == ("690261", "600181")

    def test_nun_and_poo_may_approve_below_the_line_level(self):
        for eid in ("690261", "600181"):
            assert C.can_clear_fund(self.ppl(), {}, self.LKB_ACC, 5, eid)

    def test_nobody_else_in_the_line_may(self):
        for eid in ("690604", "360002", "610055"):
            assert not C.can_clear_fund(self.ppl(), {}, self.LKB_ACC, 5, eid)

    def test_email_goes_to_nun_only(self):
        assert [p["employee_id"] for p in C.clear_notify_fund(self.ppl(), {}, self.LKB_ACC, 5)] == ["690261"]

    def test_the_holder_is_never_named_for_their_own_round(self, monkeypatch):
        monkeypatch.setitem(C.CLEAR_APPROVER_OVERRIDES, ("02", "A04"),
                            {"approvers": ("600181", "690261"), "notify": ("600181",)})
        poo_fund = _Fund("02", "A04", "600181")
        assert not C.can_clear_fund(self.ppl(), {}, poo_fund, 5, "600181")
        assert C.clear_notify_fund(self.ppl(), {}, poo_fund, 5) == []

    def test_other_funds_keep_the_chain_of_command(self):
        fund = _Fund("02", "A04", "600181")  # สระบุรี ยังไม่อยู่ในรายการ
        assert C.clear_named(fund) == ()
        assert C.can_clear_fund(self.ppl(), {}, fund, 5, "690604")
        assert not C.can_clear_fund(self.ppl(), {}, fund, 5, "690261")
