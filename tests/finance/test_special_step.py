# -*- coding: utf-8 -*-
"""ขั้นพิเศษแผนกบัญชี (ผู้ใช้สั่ง 26/10/2026): ใบของแผนกบัญชียอด 2,000.01–5,000 (ข้อ 6.6) ต้องผ่าน 690041 เป็นอีกขั้น
ต่อจากขั้นตาม TOA เช่น 610055 ขอ 1,500 นุ่น (พุทธชาติ) กดจบ · ขอ 5,000 นุ่นก่อน แล้วค่อย 690041"""
import os
from decimal import Decimal
from types import SimpleNamespace as NS

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

from services.finance import approval_logic as A  # noqa: E402
from services.finance import approval_repo  # noqa: E402
from services.finance import clear_approval_logic as clr  # noqa: E402
from services.finance import finance_mail as fm  # noqa: E402
from tests.finance.test_approval_logic import TIERS  # noqa: E402

ACC, OPS, IT, EXECS = 6, 11, 12, 99
SUK = "690041"


def person(eid, uid, level, dept, site=1, active=True):
    return {"employee_id": eid, "id": uid, "level": level, "department_id": dept, "division_id": dept + 100,
            "site_id": site, "active": active}


PEOPLE = {p["employee_id"]: p for p in (
    person("R", 1, 2, ACC, site=2),        # 610055 ผู้เบิกแผนกบัญชี ศลบ.
    person("NOON", 2, 3, ACC, site=2),     # พุทธชาติ หัวหน้าแผนกบัญชี ศลบ.
    person(SUK, 3, 4, ACC),                # สุขพัฒน์ ผู้ช่วยผู้จัดการ สกท.
    person("JIRA", 4, 4, ACC),             # จิราพร ผู้ช่วยผู้จัดการ สกท.
    person("BANK", 5, 5, ACC),             # เจษฎา ผู้จัดการ สกท.
    person("OPSR", 6, 2, OPS),
    person("OPSLEAD", 7, 3, OPS),
    person("N9", 8, 9, EXECS),
    person("670108", 9, 4, IT),            # superadmin
)}
NO_MAP = {}


def plan(who, amount, people=PEOPLE):
    return A.evaluate(TIERS, people, NO_MAP, who, Decimal(str(amount)))


def named(info):
    return [s.get("approver") for s in info["steps"]]


def round_logs(*pairs):
    return [{"id": i, "level_no": step, "action": A.ACTION_APPROVED, "action_by": PEOPLE[eid]["id"],
             "action_at": None, "remark": None} for i, (step, eid) in enumerate(pairs, start=1)]


# ---------------------------- แผนขั้น ----------------------------

def test_1500_ends_with_noon():
    info = plan("R", 1500)
    assert [s["required_level"] for s in info["steps"]] == [3] and named(info) == [None]


def test_5000_adds_690041_as_its_own_step_after_the_toa_steps():
    info = plan("R", 5000)
    assert named(info) == [None, SUK]
    assert info["steps"][1]["step"] == 2 and "690041" in info["steps"][1]["label"]
    assert info["required_level"] == 3          # ระดับ TOA ของยอดยังเป็นของขั้นตาม TOA


def test_only_the_6_6_band():
    assert named(plan("R", "2000")) == [None]
    assert named(plan("R", "2000.01"))[-1] == SUK
    assert named(plan("R", "5000.01"))[-1] is None   # 6.5 ไม่มีขั้นพิเศษ


def test_other_departments_are_untouched():
    assert named(plan("OPSR", 5000)) == [None]


def test_690041_own_request_has_no_special_step():
    assert SUK not in named(plan(SUK, 5000))


def test_inactive_690041_drops_the_step():
    people = {**PEOPLE, SUK: {**PEOPLE[SUK], "active": False}}
    assert named(plan("R", 5000, people)) == [None]


def test_the_manager_needs_690041_too():
    info = plan("BANK", 5000)                       # TOA ต้องระดับ 6 แล้วยังต้องผ่าน 690041 (ระดับ 4)
    assert named(info)[-1] == SUK
    logs = round_logs((1, "N9"))
    assert A.decide(info, PEOPLE, NO_MAP, "BANK", SUK, 2, logs)["allowed"] is True


# ---------------------------- ใครกดขั้นพิเศษได้ ----------------------------

def test_only_690041_or_superadmin_may_take_the_special_step():
    info = plan("R", 5000)
    logs = round_logs((1, "NOON"))
    assert A.decide(info, PEOPLE, NO_MAP, "R", SUK, 2, logs)["allowed"] is True
    for other in ("JIRA", "BANK", "N9", "NOON"):
        assert A.decide(info, PEOPLE, NO_MAP, "R", other, 2, logs)["allowed"] is False, other
    d = A.decide(info, PEOPLE, NO_MAP, "R", "670108", 2, logs)
    assert (d["allowed"], d["outcome"]) == (True, A.OUTCOME_APPROVED)


def test_a_higher_level_at_step_1_does_not_skip_the_special_step():
    d = A.decide(plan("R", 5000), PEOPLE, NO_MAP, "R", "BANK", 1, [])
    assert (d["allowed"], d["outcome"], d["next_step"], d["skipped"]) == (True, A.OUTCOME_STEP, 2, [])


def test_690041_at_step_1_also_covers_the_special_step():
    d = A.decide(plan("R", 5000), PEOPLE, NO_MAP, "R", SUK, 1, [])
    assert (d["allowed"], d["outcome"], d["skipped"]) == (True, A.OUTCOME_APPROVED, [2])


def test_skip_note_for_the_special_step():
    spec = plan("R", 5000)["steps"][1]
    assert "ขั้นพิเศษ" in A.skip_note(spec, 1, 4)
    normal = {"step": 2, "required_level": 5, "label": "x"}
    assert A.skip_note(normal, 1, 5) == A.skip_remark(2, 1, 5, 5)


# ---------------------------- อีเมล ----------------------------

def test_special_step_state_and_email_go_to_690041_only():
    info = plan("BANK", 5000)
    logs = round_logs((1, "N9"))
    state = A.evaluate_step(info, PEOPLE, NO_MAP, "BANK", 2, logs)
    assert state["approver"] == SUK
    el = A.eligible_approvers(PEOPLE.values(), PEOPLE["BANK"], state["required_level"], NO_MAP, state["excluded"],
                              named=state["approver"])
    got = A.notify_approvers(el, state["required_level"], PEOPLE["BANK"], named=state["approver"])
    assert [p["employee_id"] for p in got] == [SUK]


class _Q:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *a, **k):
        return self

    def all(self):
        return self.rows


class _Db:
    def __init__(self, rows):
        self.rows = rows

    def query(self, *a):
        return _Q(self.rows)


def test_step_approver_emails_for_the_special_step(monkeypatch):
    ctx = approval_repo.ApprovalContext(tiers=TIERS, people=PEOPLE, mappings=NO_MAP)
    monkeypatch.setattr(approval_repo, "load_context", lambda db, fresh=False: ctx)
    monkeypatch.setattr(approval_repo, "_amount_of", lambda db, sid: Decimal("5000"))
    monkeypatch.setattr(approval_repo, "current_round_logs", lambda db, sid: round_logs((1, "NOON")))
    users = [NS(id=3, email="suk@x.com"), NS(id=4, email="jira@x.com"), NS(id=5, email="bank@x.com")]
    sub = NS(id=1, created_by="R", current_approval_level=2)
    assert fm.step_approver_emails(_Db(users), sub) == ["suk@x.com"]


# ---------------------------- เคลียร์ ----------------------------

def test_clearing_in_the_6_6_band_needs_690041_too():
    p = clr.plan(TIERS, PEOPLE, NO_MAP, "R", Decimal("4000"))
    assert named(p) == [None, SUK]
    assert clr.can_approve(p, PEOPLE, NO_MAP, "R", SUK, step=2)
    assert not clr.can_approve(p, PEOPLE, NO_MAP, "R", "JIRA", step=2)
    assert named(clr.plan(TIERS, PEOPLE, NO_MAP, "R", Decimal("1500"))) == [None]
