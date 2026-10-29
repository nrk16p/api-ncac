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
