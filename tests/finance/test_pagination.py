"""v3 §9 server-side pagination — DB-free (SQL is compiled, never executed; the db is a fake)."""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

from datetime import date, datetime  # noqa: E402
from decimal import Decimal  # noqa: E402
from types import SimpleNamespace as NS  # noqa: E402

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402

from routes.finance import advance_routes as routes  # noqa: E402
from routes.forms import form_approval_routes as fa  # noqa: E402
from services.finance import advance_logic as L  # noqa: E402
from services.finance import advance_repo as repo  # noqa: E402
from services.finance import approval_repo as AR  # noqa: E402

APPROVES = ("In Progress", "Rejected", "Approved")
FINS = [None, "VOUCHERED", "VOUCHER_REJECTED", "RETURNED", "RESUBMITTED", "PAID", "CLEARING_SUBMITTED",
        "SENT_BACK", "CLOSED"]


def spec_matches(spec, approve, fin):
    if spec is None or approve not in spec["approve"]:
        return False
    if spec["fin"] is None:
        return True
    return (fin is None and spec["fin_null"]) or (fin is not None and fin in spec["fin"])


# ---------------------------- status -> condition agrees with derive_status ----------------------------

@pytest.mark.parametrize("approve", APPROVES)
@pytest.mark.parametrize("fin", FINS)
def test_status_spec_agrees_with_derive_status(approve, fin):
    derived, _ = L.derive_status(approve, fin, None, date(2026, 10, 6))
    for status in L.ALL_STATUSES:
        assert spec_matches(L.status_condition_spec(status), approve, fin) == (status == derived), (status, approve, fin)


def test_every_status_has_a_spec_and_unknown_has_none():
    assert all(L.status_condition_spec(s) for s in L.ALL_STATUSES)
    assert L.status_condition_spec("NOPE") is None


@pytest.mark.parametrize("approve", APPROVES)
@pytest.mark.parametrize("fin", FINS)
def test_overdue_fin_statuses_agree_with_derive_status(approve, fin):
    due, today = date(2026, 10, 1), date(2026, 10, 6)
    _, overdue = L.derive_status(approve, fin, due, today)
    assert overdue == (approve == "Approved" and fin in L.OVERDUE_FIN_STATUSES)


def test_sql_clauses_compile():
    sql = str(repo.status_clause(["AWAITING_VOUCHER", "PENDING_APPROVAL"]).compile(dialect=postgresql.dialect()))
    assert "fin_advances.id IS NULL" in sql and "form_submissions.status_approve IN" in sql
    assert str(repo.status_clause(["NOPE"]).compile(dialect=postgresql.dialect())) == "false"
    od = str(repo.overdue_clause(date(2026, 10, 6)).compile(dialect=postgresql.dialect()))
    assert "fin_advances.clear_due_date <" in od and "fin_advances.fin_status IN" in od


# ---------------------------- summary fold ----------------------------

def test_fold_summary_counts_overdue_and_outstanding():
    rows = [("In Progress", None, 3, 0, None),
            ("Approved", None, 2, 0, None),
            ("Approved", "PAID", 4, 1, Decimal("1000.50")),
            ("Approved", "RESUBMITTED", 1, 0, Decimal("10")),
            ("Approved", "CLOSED", 5, 0, Decimal("9999")),
            ("Rejected", None, 1, 0, None)]
    s = L.fold_summary(rows)
    assert set(s["counts"]) == set(L.STATUS_LABELS)
    assert s["counts"]["PENDING_APPROVAL"] == 3 and s["counts"]["AWAITING_VOUCHER"] == 3
    assert s["counts"]["AWAITING_CLEARING"] == 4 and s["counts"]["CLOSED"] == 5 and s["counts"]["SENT_BACK"] == 0
    assert s["overdue"] == 1
    assert s["outstanding_amount"] == 1010.5  # fin rows not CLOSED; no fin row excluded


# ---------------------------- q + param validation ----------------------------

def test_escape_like_and_clean_q():
    assert L.escape_like("50%_off\\") == "50\\%\\_off\\\\"
    assert len(L.clean_q("a" * 500)) == 100
    assert L.clean_q("   ") is None and L.clean_q(None) is None


def test_search_condition_escapes_wildcards():
    cond = repo._search_condition("100%_")
    compiled = cond.compile(dialect=postgresql.dialect())
    assert "100\\%\\_" in " ".join(str(v) for v in compiled.params.values())
    assert "ESCAPE" in str(compiled)


def test_parse_status_list():
    known, unknown = L.parse_status_list("AWAITING_CLEARING, SENT_BACK,BAD")
    assert known == ["AWAITING_CLEARING", "SENT_BACK"] and unknown == ["BAD"]
    assert L.parse_status_list(None) == ([], [])


def _bounds(func, name):
    import inspect
    out = {}
    for m in inspect.signature(func).parameters[name].default.metadata:
        for attr in ("ge", "le"):
            if hasattr(m, attr):
                out[attr] = getattr(m, attr)
    return out


def test_declared_param_bounds():
    """FastAPI answers 422 outside these bounds (no clamping)."""
    assert _bounds(routes.list_advances, "page") == {"ge": 1}
    assert _bounds(routes.list_advances, "page_size") == {"ge": 1, "le": 200}
    assert _bounds(fa.get_approval_history, "page") == {"ge": 1}
    assert _bounds(fa.get_approval_history, "page_size") == {"ge": 1, "le": 200}
    import inspect
    assert inspect.signature(routes.list_advances).parameters["page_size"].default.default == 20


def _call(**kw):
    base = dict(status=None, overdue=None, employee_id=None, acc_code=None, date_from=None, date_to=None,
                cost_center=None, q=None, department=None, sort="desc", page=None, page_size=20, db=object())
    base.update(kw)
    return routes.list_advances(**base)


def test_unknown_status_is_422_in_paged_mode(monkeypatch):
    monkeypatch.setattr(repo, "list_advances_page", lambda *a, **k: pytest.fail("must not query"))
    with pytest.raises(HTTPException) as e:
        _call(page=1, status="BAD")
    assert e.value.status_code == 422


def test_paged_mode_passes_params(monkeypatch):
    seen = {}
    monkeypatch.setattr(repo, "list_advances_page", lambda db, **k: seen.update(k) or {"items": [], "total": 0})
    _call(page=2, page_size=50, status="SENT_BACK,CLOSED", overdue=True, q="ab", cost_center="CC1",
          employee_id="E1", date_from=date(2026, 10, 1), department="บัญชี", sort="asc")
    assert seen["department"] == "บัญชี" and seen["sort"] == "asc"
    assert seen["page"] == 2 and seen["page_size"] == 50 and seen["statuses"] == ["SENT_BACK", "CLOSED"]
    assert seen["overdue"] is True and seen["q"] == "ab" and seen["cost_center"] == "CC1"
    assert seen["date_from"] == date(2026, 10, 1) and seen["employee_id"] == "E1"


def test_without_page_returns_the_old_array(monkeypatch):
    seen = {}
    monkeypatch.setattr(repo, "list_advances", lambda db, **k: seen.update(k) or [{"form_id": "ADV-1"}])
    monkeypatch.setattr(repo, "list_advances_page", lambda *a, **k: pytest.fail("paged path must stay off"))
    assert _call(status="CLOSED", page_size=10) == [{"form_id": "ADV-1"}]
    assert seen["status"] == "CLOSED"


# ---------------------------- load_context cache ----------------------------

def test_load_context_cached_for_60s(monkeypatch):
    AR.clear_context_cache()
    now = [1000.0]
    monkeypatch.setattr(AR, "_clock", lambda: now[0])
    calls = []
    monkeypatch.setattr(AR, "load_tiers", lambda db: calls.append("t") or ["tier"])
    monkeypatch.setattr(AR, "load_people", lambda db: {})
    monkeypatch.setattr(AR, "load_mappings", lambda db: {})
    first = AR.load_context(object())
    now[0] += 59
    assert AR.load_context(object()) is first and calls == ["t"]
    now[0] += 2
    second = AR.load_context(object())
    assert second is not first and calls == ["t", "t"]
    AR.clear_context_cache()
    AR.load_context(object())
    assert calls == ["t", "t", "t"]
    AR.clear_context_cache()


# ---------------------------- approval-history paging ----------------------------

def test_latest_log_per_submission_keeps_first_seen():
    assert fa.latest_log_per_submission([(11, 5), (12, 5), (3, 7), (2, 5)]) == [11, 3]


class _Q:
    def __init__(self, result, log):
        self.result, self.log = result, log

    def __getattr__(self, name):
        def chain(*a, **k):
            self.log.append(name)
            return self
        return chain

    def all(self):
        return self.result


def _sub(i, creator):
    return NS(id=i, form_id=f"F{i}", form=NS(form_code="IT", form_name="n"), current_approval_level=1, status="Open",
              status_approve="Approved", created_by=creator, created_at=None)


def test_history_page_slices_and_batches_users(monkeypatch):
    approver = NS(id=9, employee_id="A1", firstname="ap", lastname="pr")
    users = {"R1": NS(employee_id="R1", firstname="x", lastname="y", email="e", image_url=None, department_id=None),
             "R2": NS(employee_id="R2", firstname="z", lastname="w", email="e2", image_url=None, department_id=None)}
    user_calls = []

    def fake_users(db, ids):
        ids = list(ids)
        user_calls.append(ids)
        if ids == ["A1"]:
            return {"A1": approver}
        return {k: v for k, v in users.items() if k in ids}

    monkeypatch.setattr(fa, "users_by_employee_ids", fake_users)
    monkeypatch.setattr(fa, "_get_request_cache", lambda db: {})
    monkeypatch.setattr(fa, "_load_departments", lambda db, cache: {})
    # ordered (log_id, submission_id): sub 5 appears twice -> 4 distinct submissions [10, 20, 30, 40]
    light = [(10, 5), (11, 5), (20, 6), (30, 7), (40, 8)]
    logs = {i: NS(id=i, level_no=1, action="APPROVED", action_at=datetime(2026, 10, 1), remark=None)
            for i in (20, 30)}
    page_rows = [(logs[30], _sub(7, "R2")), (logs[20], _sub(6, "R1"))]  # DB order != page order
    queries = iter([_Q(light, []), _Q(page_rows, [])])
    db = NS(query=lambda *e: next(queries))

    out = fa.get_approval_history(employee_id="A1", start_date=None, end_date=None, page=2, page_size=2, db=db)
    assert out["total"] == 4 and out["page"] == 2 and out["page_size"] == 2
    # page 2 of the kept ids [10, 20, 30, 40] is [30, 40]; the fake holds only 30 and 20, so only 30 is returned
    assert [i["form_id"] for i in out["items"]] == ["F7"]
    assert user_calls == [["A1"], ["R2"]]  # one batched lookup for the requesters, not one per row


def test_history_page_unknown_approver():
    db = NS(query=lambda *e: _Q([], []))
    import routes.forms.form_approval_routes as m
    orig = m.users_by_employee_ids
    m.users_by_employee_ids = lambda db, ids: {}
    try:
        out = fa.get_approval_history(employee_id="X", start_date=None, end_date=None, page=1, page_size=20, db=db)
    finally:
        m.users_by_employee_ids = orig
    assert out == {"items": [], "total": 0, "page": 1, "page_size": 20}


# ---------------------------- FE contract additions ----------------------------

def _sql(query_or_clause):
    return str(query_or_clause.compile(dialect=postgresql.dialect()))


def _session():
    from sqlalchemy.orm import Session
    return Session()


def _ents():
    from models.finance_model import FinAdvance
    from models.master_model import FormSubmission
    return (FormSubmission, FinAdvance)


def test_department_filter_is_exact_name_match():
    q = repo._scoped_query(_session(), _ents(), department="บัญชี")
    sql = _sql(q.statement)
    # users/departments are aliased inside the EXISTS (so it never auto-correlates to an outer users join)
    assert "department_name_th = " in sql and "department_id = users_" in sql and "EXISTS" in sql


def test_q_matches_voucher_no():
    assert "fin_advances.voucher_no ILIKE" in _sql(repo._search_condition("SADV"))


def test_sort_param_declared_and_order_applied(monkeypatch):
    import inspect
    assert inspect.signature(routes.list_advances).parameters["sort"].default.default == "desc"

    class Q:
        def __init__(self):
            self.orders = []

        def __getattr__(self, n):
            def f(*a, **k):
                if n == "order_by":
                    self.orders.append(a)
                return self
            return f

        def scalar(self):
            return 0

        def all(self):
            return []

    seen = []

    def fake_scoped(db, ents, **kw):
        q = Q()
        seen.append(q)
        return q
    monkeypatch.setattr(repo, "_scoped_query", fake_scoped)
    monkeypatch.setattr(repo, "_serialize_rows", lambda db, rows: [])
    monkeypatch.setattr(repo, "_filter_options", lambda db, **k: {"cost_centers": [], "departments": []})
    for direction in ("asc", "desc"):
        seen.clear()
        out = repo.list_advances_page(NS(), page=1, page_size=5, sort=direction)
        page_q = [q for q in seen if any(len(o) == 2 for o in q.orders)][0]
        modifiers = [str(c.modifier.__name__ if hasattr(c.modifier, "__name__") else c.modifier)
                     for c in page_q.orders[-1]]
        assert modifiers == [("asc_op" if direction == "asc" else "desc_op")] * 2
        assert set(out) == {"items", "total", "page", "page_size", "summary", "options"}


def test_filter_options_scope_excludes_status_cost_center_department(monkeypatch):
    got = []

    class Q:
        def __getattr__(self, n):
            return lambda *a, **k: self

        def all(self):
            return [("B",), ("A",), ("B",)]

    monkeypatch.setattr(repo, "_scoped_query", lambda db, ents, **kw: got.append(kw) or Q())
    out = repo._filter_options(NS(), employee_id="E", q="x")
    assert out == {"cost_centers": ["A", "B"], "departments": ["A", "B"]}
    assert all(set(kw) == {"employee_id", "q"} for kw in got)


def test_paged_scope_excludes_filters_for_options(monkeypatch):
    seen = {}
    monkeypatch.setattr(repo, "_filter_options", lambda db, **k: seen.update(k) or {})
    monkeypatch.setattr(repo, "_scoped_query", lambda *a, **k: _EmptyQ())
    monkeypatch.setattr(repo, "_serialize_rows", lambda db, rows: [])
    repo.list_advances_page(NS(), page=1, page_size=5, employee_id="E", q="x", cost_center="C", department="D",
                            acc_code=None, date_from=date(2026, 10, 1))
    assert "cost_center" not in seen and "department" not in seen
    assert seen["employee_id"] == "E" and seen["q"] == "x" and seen["date_from"] == date(2026, 10, 1)


class _EmptyQ:
    def __getattr__(self, n):
        return lambda *a, **k: self

    def all(self):
        return []

    def scalar(self):
        return 0


def _history_db(scope_seen):
    class LQ:
        def __getattr__(self, n):
            def f(*a, **k):
                scope_seen.append((n, " ".join(_sql(x) for x in a if hasattr(x, "compile"))))
                return self
            return f

        def all(self):
            return []
    return NS(query=lambda *e: LQ())


@pytest.mark.parametrize("scope,op", [("advance", "form_masters.form_type ="), ("it", "form_masters.form_type !=")])
def test_history_scope_filters_in_sql_before_paging(monkeypatch, scope, op):
    approver = NS(id=9, employee_id="A1", firstname="a", lastname="b")
    monkeypatch.setattr(fa, "users_by_employee_ids", lambda db, ids: {"A1": approver})
    seen = []
    out = fa.get_approval_history(employee_id="A1", start_date=None, end_date=None, page=1, page_size=5,
                                  scope=scope, db=_history_db(seen))
    assert out["total"] == 0
    assert any(op in clause for _, clause in seen), seen


def test_history_scope_all_adds_no_form_join(monkeypatch):
    approver = NS(id=9, employee_id="A1", firstname="a", lastname="b")
    monkeypatch.setattr(fa, "users_by_employee_ids", lambda db, ids: {"A1": approver})
    seen = []
    fa.get_approval_history(employee_id="A1", start_date=None, end_date=None, page=1, page_size=5, scope="all",
                            db=_history_db(seen))
    assert not any("form_masters" in clause for _, clause in seen)
    import inspect
    assert inspect.signature(fa.get_approval_history).parameters["scope"].default.default == "all"


def test_search_and_department_exists_survive_an_outer_users_join():
    """Regression: the options/summary queries join users; an un-aliased EXISTS auto-correlated to it and lost
    its FROM ("returned no FROM clauses due to auto-correlation")."""
    from models.user_model import User
    q = repo._scoped_query(_session(), _ents(), department="บัญชี", q="9002")
    q = q.join(User, User.employee_id == FormSubmission_created_by())
    _sql(q.statement)  # must compile


def FormSubmission_created_by():
    from models.master_model import FormSubmission
    return FormSubmission.created_by


def test_answer_exists_survives_an_outer_values_join():
    """Regression: the cost-center options query joins form_submission_values/form_questions; the purpose and
    cost-center EXISTS must not auto-correlate to them."""
    from models.master_model import FormSubmissionValue, FormQuestion
    q = repo._scoped_query(_session(), _ents(), cost_center="สกท", q="กาแฟ")
    q = q.join(FormSubmissionValue, FormSubmissionValue.submission_id == FormSubmission_id()).join(
        FormQuestion, FormQuestion.id == FormSubmissionValue.question_id)
    _sql(q.statement)  # must compile


def FormSubmission_id():
    from models.master_model import FormSubmission
    return FormSubmission.id
