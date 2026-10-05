"""v3 §4 ADV-YYMM-NNN doc number + the approval fields on the finance routes (DB-free)."""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

from datetime import datetime, timezone  # noqa: E402
from types import SimpleNamespace as NS  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import pytest  # noqa: E402

from routes.finance import advance_routes as ar  # noqa: E402
from routes.forms import form_submission_routes as sr  # noqa: E402
from services.finance.advance_logic import AdvanceRuleError  # noqa: E402


class _SeqQuery:
    def __init__(self, db):
        self.db = db

    def filter(self, *conds):
        self.db.filters.append(conds)
        return self

    def with_for_update(self):
        self.db.locked = True
        return self

    def first(self):
        return self.db.seq


class _SeqDb:
    def __init__(self, seq=None):
        self.seq = seq
        self.filters = []
        self.locked = False
        self.added = []

    def query(self, *a):
        return _SeqQuery(self)

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass


def _year_key(db):
    (code_cond, year_cond), = db.filters
    return code_cond.right.value, year_cond.right.value


@pytest.fixture
def october_2026(monkeypatch):
    monkeypatch.setattr(sr, "_now_bkk", lambda: datetime(2026, 10, 5, 9, 0, tzinfo=ZoneInfo("Asia/Bangkok")))


def test_adv_first_of_month_creates_yymm_sequence(october_2026):
    db = _SeqDb()
    assert sr.generate_form_id(db, "ADV") == "ADV-2610-001"
    assert _year_key(db) == ("ADV", 2610) and db.locked
    assert (db.added[0].form_code, db.added[0].year, db.added[0].last_number) == ("ADV", 2610, 1)


def test_adv_continues_existing_sequence(october_2026):
    seq = NS(form_code="ADV", year=2610, last_number=41)
    assert sr.generate_form_id(_SeqDb(seq), "ADV") == "ADV-2610-042"
    assert seq.last_number == 42


def test_adv_past_999_grows_to_four_digits(october_2026):
    assert sr.generate_form_id(_SeqDb(NS(last_number=999)), "ADV") == "ADV-2610-1000"


def test_adv_month_uses_bangkok_wall_clock(monkeypatch):
    # 2026-10-31 18:00 UTC is already 1 Nov in Bangkok
    monkeypatch.setattr(sr, "_now_bkk",
                        lambda: datetime(2026, 10, 31, 18, 0, tzinfo=timezone.utc).astimezone(ZoneInfo("Asia/Bangkok")))
    db = _SeqDb()
    assert sr.generate_form_id(db, "ADV") == "ADV-2611-001"
    monkeypatch.setattr(sr, "_now_bkk", lambda: datetime(2027, 1, 2, tzinfo=ZoneInfo("Asia/Bangkok")))
    assert sr.generate_form_id(_SeqDb(), "ADV") == "ADV-2701-001"


def test_now_bkk_is_aware_bangkok():
    assert sr._now_bkk().utcoffset().total_seconds() == 7 * 3600


def test_other_codes_unchanged(october_2026):
    year = datetime.utcnow().year
    db = _SeqDb()
    assert sr.generate_form_id(db, "IT") == f"IT-{year}-0001"
    assert _year_key(db) == ("IT", year)
    assert sr.generate_form_id(_SeqDb(NS(last_number=11)), "IT") == f"IT-{year}-0012"


# ---------------------------- finance routes ----------------------------

STEPS = [{"step": 1, "required_level": 4, "label": "หัวหน้าระดับ 4 ขึ้นไป"},
         {"step": 2, "required_level": 5, "label": "ระดับ 5 ขึ้นไป (ข้อ 6.5 · MGR)"}]


def test_approval_preview_adds_steps(monkeypatch):
    monkeypatch.setattr(ar.approval_repo, "describe", lambda db, e, a: {
        "clause": "6.5", "approver_label": "MGR", "min_level": 5, "required_level": 5, "direct_level": 5,
        "steps": STEPS})
    assert ar.approval_preview("E1", "15000", None) == {
        "clause": "6.5", "approver_label": "MGR", "required_level": 5, "steps": STEPS}


def test_detail_approval_block(monkeypatch):
    sub = NS(id=5)
    monkeypatch.setattr(ar.repo, "get_advance_detail",
                        lambda db, f: {"requester": {"employee_id": "R"}, "request": {"amount": 15000.0}})
    monkeypatch.setattr(ar.repo, "get_advance_submission", lambda db, f: sub)
    calls = []

    def fake(db, s, amount):
        calls.append((s, amount))
        return {"clause": "6.5", "steps": STEPS, "current_step": 1, "step_approvals": []}

    monkeypatch.setattr(ar.approval_repo, "detail_approval", fake)
    detail = ar.get_advance("ADV-2610-001", None)
    assert calls == [(sub, 15000.0)]
    assert detail["approval"]["current_step"] == 1 and detail["approval"]["steps"] == STEPS


def test_detail_approval_none_on_rule_error(monkeypatch):
    monkeypatch.setattr(ar.repo, "get_advance_detail",
                        lambda db, f: {"requester": {"employee_id": "R"}, "request": {"amount": None}})
    monkeypatch.setattr(ar.repo, "get_advance_submission", lambda db, f: NS(id=5))

    def boom(*a):
        raise AdvanceRuleError("x")

    monkeypatch.setattr(ar.approval_repo, "detail_approval", boom)
    assert ar.get_advance("ADV-2610-001", None)["approval"] is None


# ---------------------------- /forms/approval-history: one row per submission ----------------------------

from datetime import datetime as _dt  # noqa: E402

from sqlalchemy.sql import operators  # noqa: E402

from routes.forms import form_approval_routes as fa  # noqa: E402


class _HistoryQuery:
    """Returns the rows ordered the way the database would by the recorded ORDER BY (a stable multi-key sort);
    ties nobody ordered keep the given order, which is arbitrary in Postgres."""
    def __init__(self, rows):
        self.rows, self.clauses = rows, []

    def join(self, *a, **k):
        return self

    def options(self, *a):
        return self

    def filter(self, *a):
        return self

    def order_by(self, *clauses):
        self.clauses.extend(clauses)
        return self

    def all(self):
        rows = list(self.rows)
        for clause in reversed(self.clauses):
            key = clause.element.key
            rows = sorted(rows, key=lambda r: getattr(r[0], key), reverse=clause.modifier is operators.desc_op)
        return rows


def test_approval_history_keeps_the_approvers_own_log_on_a_skip(monkeypatch):
    """A dynamic skip writes the approver's step-1 log and a system step-2 log in one transaction: same action_at.
    The history row for that submission must be the approver's own (step 1, their remark)."""
    approver = NS(id=101, firstname="ก", lastname="ข")
    monkeypatch.setattr(fa, "get_user_by_employee_id", lambda db, eid: approver if eid == "A1" else None)
    monkeypatch.setattr(fa, "_get_request_cache", lambda db: {})
    monkeypatch.setattr(fa, "_load_departments", lambda db, cache: {})
    sub = NS(id=5, form_id="ADV-2610-001", form=NS(form_code="ADV", form_name="เบิกเงิน"), current_approval_level=1,
             status="Open", status_approve="Approved", created_by="R1", created_at=None)
    older = NS(id=7, form_id="ADV-2610-000", form=NS(form_code="ADV", form_name="เบิกเงิน"), current_approval_level=1,
               status="Open", status_approve="Approved", created_by="R1", created_at=None)
    at = _dt(2026, 10, 5, 9, 0)
    own = NS(id=11, level_no=1, action="APPROVED", action_at=at, remark="อนุมัติครับ")
    skip = NS(id=12, level_no=2, action="APPROVED", action_at=at, remark="ผ่านขั้น 2 พร้อมการอนุมัติขั้น 1: …")
    earlier = NS(id=3, level_no=1, action="APPROVED", action_at=_dt(2026, 10, 1), remark="ok")
    query = _HistoryQuery([(skip, sub), (own, sub), (earlier, older)])  # the tie arrives skip-first
    db = NS(query=lambda *entities: query)
    out = fa.get_approval_history(employee_id="A1", start_date=None, end_date=None, db=db)
    assert [(r["form_id"], r["level_no"], r["remark"]) for r in out] == [
        ("ADV-2610-001", 1, "อนุมัติครับ"), ("ADV-2610-000", 1, "ok")]
    assert [(c.element.key, c.modifier) for c in query.clauses] == [("action_at", operators.desc_op),
                                                                     ("id", operators.asc_op)]
