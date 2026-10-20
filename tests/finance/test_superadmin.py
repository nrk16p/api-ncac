"""narongkorn.a (670108) เป็น superadmin ของงานเบิก Advance: ทำได้ทุกปุ่ม รวมอนุมัติทุกขั้น (ผู้ใช้สั่ง 20/10/2026).

อนุมัติแทนได้ทุกขั้น แต่ผ่านแค่ขั้นที่กด ไม่ข้ามขั้นถัดไปด้วยระดับของตัวเอง และไม่อนุมัติใบของตัวเอง
อีเมลกับการเลือกผู้อนุมัติปกติไม่เปลี่ยน (superadmin ไม่ได้อีเมลทุกใบ)
"""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

import pytest  # noqa: E402

from services.finance import approval_logic as rules  # noqa: E402
from services.finance import clear_approval_logic as clr  # noqa: E402
from services.finance import permission_logic as perms  # noqa: E402

SA = "670108"


def person(eid, uid, level, dept, active=True):
    return {"employee_id": eid, "id": uid, "level": level, "department_id": dept, "active": active}


PEOPLE = {
    "REQ": person("REQ", 1, 2, 6),          # ผู้เบิกแผนกบัญชี ระดับ 2
    "LEAD": person("LEAD", 2, 4, 6),        # หัวหน้าระดับ 4
    "CL": person("CL", 3, 8, 1),            # ผู้บริหารระดับ 8
    SA: person(SA, 7, 3, 11),               # superadmin แผนก 11 ระดับ 3
}
STEPS = [{"step": 1, "required_level": 4, "label": "ขั้น 1"}, {"step": 2, "required_level": 8, "label": "ขั้น 2"}]
EVAL = {"steps": STEPS, "clause": "6.4", "approver_label": "CL", "required_level": 8}


def test_is_superadmin():
    assert perms.is_superadmin(SA) and perms.is_superadmin(670108)
    assert not perms.is_superadmin("690041") and not perms.is_superadmin(None)


@pytest.mark.parametrize("perm", perms.ALL_PERMISSIONS)
def test_superadmin_holds_every_permission_even_when_configured(perm):
    grants = {p: {"690041"} for p in perms.ALL_PERMISSIONS}
    assert perms.allowed(perm, SA, grants, is_finance=False)
    assert not perms.allowed(perm, "999999", grants, is_finance=True)


def test_permissions_for_lists_all():
    assert perms.permissions_for(SA, {}, is_finance=False) == list(perms.ALL_PERMISSIONS)


def test_superadmin_may_approve_a_step_above_their_level():
    d = rules.decide(EVAL, PEOPLE, {}, "REQ", SA, 0)
    assert d["allowed"] and d["step"] == 1


def test_superadmin_approval_does_not_skip_the_next_step():
    d = rules.decide(EVAL, PEOPLE, {}, "REQ", SA, 0)
    assert d["outcome"] == rules.OUTCOME_STEP and d["next_step"] == 2 and d["skipped"] == []


def test_superadmin_may_approve_step_two_after_approving_step_one():
    logs = [{"level_no": 1, "action": rules.ACTION_APPROVED, "action_by": 7}]
    d = rules.decide(EVAL, PEOPLE, {}, "REQ", SA, 2, logs)
    assert d["allowed"] and d["step"] == 2 and d["outcome"] == rules.OUTCOME_APPROVED


def test_superadmin_cannot_approve_own_request():
    d = rules.decide(EVAL, PEOPLE, {}, SA, SA, 0)
    assert not d["allowed"]


def test_ordinary_people_unchanged():
    assert rules.decide(EVAL, PEOPLE, {}, "REQ", "LEAD", 0)["allowed"]
    assert not rules.decide(EVAL, PEOPLE, {}, "REQ", "REQ", 0)["allowed"]


def test_superadmin_is_not_added_to_the_email_list():
    eligible = rules.eligible_approvers(PEOPLE.values(), PEOPLE["REQ"], 4, {})
    assert SA not in {p["employee_id"] for p in eligible}


def test_superadmin_may_approve_any_clearing_step():
    info = {"clause": "6.4", "steps": [{"step": 1, "required_level": 4}, {"step": 2, "required_level": 8}]}
    assert clr.can_approve(info, PEOPLE, {}, "REQ", SA, 1)
    assert clr.can_approve(info, PEOPLE, {}, "REQ", SA, 2, (7,))
    assert not clr.can_approve(info, PEOPLE, {}, SA, SA, 1)


# ---- /finance/permissions/me ต้องส่งสิทธิ์ครบให้ superadmin ไม่งั้นหน้าเว็บซ่อนทุกปุ่ม (เจอจริงกับ ADV-2610-008) ----

class _Q:
    def __init__(self, row): self.row = row
    def filter(self, *a): return self
    def first(self): return self.row


class _Db:
    def __init__(self, row): self.row = row
    def query(self, *a): return _Q(self.row)


def test_permissions_me_gives_superadmin_every_button(monkeypatch):
    from types import SimpleNamespace as NS
    from routes.finance import advance_routes as ar
    monkeypatch.setattr(ar.permission_repo, "load_grants", lambda db, **k: {"voucher": {"690041"}})
    out = ar.my_permissions(SA, _Db(NS(employee_id=SA, department_id=11)))
    assert out["is_finance"] is True
    assert out["permissions"] == list(perms.ALL_PERMISSIONS)


def test_permissions_me_outsider_still_gets_nothing(monkeypatch):
    from types import SimpleNamespace as NS
    from routes.finance import advance_routes as ar
    monkeypatch.setattr(ar.permission_repo, "load_grants", lambda db, **k: {})
    out = ar.my_permissions("680043", _Db(NS(employee_id="680043", department_id=11)))
    assert out == {"employee_id": "680043", "is_finance": False, "permissions": []}
