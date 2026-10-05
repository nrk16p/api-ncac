"""v3 §7 finance email notifications: OFF by default (SMTP never constructed), renders, recipients."""
import logging
import os
from types import SimpleNamespace as NS

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

import inspect  # noqa: E402
import smtplib  # noqa: E402

import pytest  # noqa: E402
from fastapi import BackgroundTasks  # noqa: E402

from routes.finance import advance_routes as ar  # noqa: E402
from routes.finance import payee_routes as pr  # noqa: E402
from routes.forms import form_approval_routes as fa  # noqa: E402
from routes.forms import form_submission_routes as fs  # noqa: E402
from services.finance import approval_repo  # noqa: E402
from services.finance import finance_mail as fm  # noqa: E402

ENV = ("FINANCE_EMAIL_ENABLED", "FINANCE_SMTP_USER", "FINANCE_SMTP_PASSWORD", "FE_BASE_URL")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def smtp(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, *a, **k):
            calls.append(("init", a, k))

        def starttls(self):
            calls.append(("starttls",))

        def login(self, u, p):
            calls.append(("login", u, p))

        def sendmail(self, frm, to, body):
            calls.append(("sendmail", frm, to, body))

        def quit(self):
            calls.append(("quit",))

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return calls


class Boom:
    """A db / submission that must never be touched."""
    def __getattr__(self, name):
        raise AssertionError(f"touched {name}")


# ---------------------------- the switch ----------------------------

@pytest.mark.parametrize("value", [None, "", "false", "0", "yes", "tru", "1"])
def test_flag_off_never_constructs_smtp(monkeypatch, smtp, value):
    if value is not None:
        monkeypatch.setenv("FINANCE_EMAIL_ENABLED", value)
    monkeypatch.setenv("FINANCE_SMTP_USER", "u@x.com")
    monkeypatch.setenv("FINANCE_SMTP_PASSWORD", "pw")
    fm.send_finance_email("a@x.com", "s", "<p>x</p>")
    assert smtp == []


@pytest.mark.parametrize("value", ["true", "TRUE", " True "])
def test_flag_on_sends_with_finance_credentials(monkeypatch, smtp, value):
    monkeypatch.setenv("FINANCE_EMAIL_ENABLED", value)
    monkeypatch.setenv("FINANCE_SMTP_USER", "fin@x.com")
    monkeypatch.setenv("FINANCE_SMTP_PASSWORD", "pw")
    monkeypatch.setenv("SMTP_USER", "it@x.com")  # the IT sender must never be used
    fm.send_finance_email(["a@x.com", "b@x.com"], "หัวเรื่อง\r\nBcc: evil@x.com", "<p>x</p>")
    init = smtp[0]
    assert init[0] == "init" and init[2] == {"timeout": 30}
    assert ("login", "fin@x.com", "pw") in smtp
    sent = [c for c in smtp if c[0] == "sendmail"]
    assert [(c[1], c[2]) for c in sent] == [("fin@x.com", ["a@x.com"]), ("fin@x.com", ["b@x.com"])]
    for c in sent:
        assert "\nBcc:" not in c[3] and "it@x.com" not in c[3]


def _to_headers(body):
    return [line for line in body.splitlines() if line.startswith("To:")]


@pytest.mark.parametrize("n", [1, 3, 20])
def test_one_message_per_recipient_over_one_connection(monkeypatch, smtp, n):
    """Nobody sees the other recipients: N recipients -> N sends, each with a single To."""
    _enable(monkeypatch)
    addrs = [f"u{i}@x.com" for i in range(n)]
    fm.send_finance_email(addrs, "s", "<p>x</p>")
    sent = [c for c in smtp if c[0] == "sendmail"]
    assert [c[2] for c in sent] == [[a] for a in addrs]
    for addr, c in zip(addrs, sent):
        assert _to_headers(c[3]) == [f"To: {addr}"]
        assert not any(other in c[3] for other in addrs if other != addr)
    assert [c[0] for c in smtp].count("init") == 1 and [c[0] for c in smtp].count("login") == 1
    assert smtp[-1] == ("quit",)


def test_per_recipient_sends_keep_the_cap_and_dedupe(monkeypatch, smtp):
    _enable(monkeypatch)
    fm.send_finance_email(["a@x.com", "A@x.com", "", None] + [f"u{i}@x.com" for i in range(30)], "s", "h")
    sent = [c[2] for c in smtp if c[0] == "sendmail"]
    assert len(sent) == fm.MAX_RECIPIENTS and sent[0] == ["a@x.com"]


def test_one_failed_recipient_does_not_stop_the_others(monkeypatch, caplog):
    _enable(monkeypatch)
    calls = []

    class FlakySMTP:
        def __init__(self, *a, **k):
            calls.append(("init",))

        def starttls(self):
            pass

        def login(self, u, p):
            pass

        def sendmail(self, frm, to, body):
            if to == ["bad@x.com"]:
                raise smtplib.SMTPRecipientsRefused({"bad@x.com": (550, b"no such user")})
            calls.append(("sendmail", to))

        def quit(self):
            calls.append(("quit",))

    monkeypatch.setattr(smtplib, "SMTP", FlakySMTP)
    with caplog.at_level(logging.ERROR):
        fm.send_finance_email(["a@x.com", "bad@x.com", "c@x.com"], "s", "h")  # must not raise
    assert [c[1] for c in calls if c[0] == "sendmail"] == [["a@x.com"], ["c@x.com"]]
    assert calls.count(("init",)) == 2  # a fresh connection after the failure
    assert any("bad@x.com" in r.getMessage() for r in caplog.records)


def test_connection_failure_skips_the_batch_without_retrying_per_recipient(monkeypatch):
    _enable(monkeypatch)
    attempts = []

    def boom(*a, **k):
        attempts.append(1)
        raise OSError("down")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    fm.send_finance_email(["a@x.com", "b@x.com", "c@x.com"], "s", "h")  # must not raise
    assert attempts == [1]


def test_login_failure_closes_the_connection(monkeypatch):
    _enable(monkeypatch)
    calls = []

    class BadLogin:
        def __init__(self, *a, **k):
            calls.append("init")

        def starttls(self):
            pass

        def login(self, u, p):
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

        def sendmail(self, *a):
            calls.append("sendmail")

        def quit(self):
            calls.append("quit")

    monkeypatch.setattr(smtplib, "SMTP", BadLogin)
    fm.send_finance_email(["a@x.com", "b@x.com"], "s", "h")
    assert calls == ["init", "quit"]


@pytest.mark.parametrize("user,pw", [(None, "pw"), ("u@x.com", None), (None, None), ("", "")])
def test_enabled_without_credentials_warns_and_skips(monkeypatch, smtp, caplog, user, pw):
    monkeypatch.setenv("FINANCE_EMAIL_ENABLED", "true")
    monkeypatch.setenv("SMTP_USER", "it@x.com")
    monkeypatch.setenv("SMTP_PASSWORD", "itpw")
    for k, v in (("FINANCE_SMTP_USER", user), ("FINANCE_SMTP_PASSWORD", pw)):
        if v is not None:
            monkeypatch.setenv(k, v)
    with caplog.at_level(logging.WARNING):
        fm.send_finance_email("a@x.com", "s", "h")
    assert smtp == [] and any("missing" in r.message for r in caplog.records)


def test_send_swallows_every_exception(monkeypatch):
    monkeypatch.setenv("FINANCE_EMAIL_ENABLED", "true")
    monkeypatch.setenv("FINANCE_SMTP_USER", "u@x.com")
    monkeypatch.setenv("FINANCE_SMTP_PASSWORD", "pw")

    def boom(*a, **k):
        raise OSError("down")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    fm.send_finance_email("a@x.com", "s", "h")  # must not raise


def test_queue_event_flag_off_touches_nothing(smtp):
    bg = BackgroundTasks()
    fm.queue_event(bg, Boom(), fm.SUBMITTED, Boom())
    assert bg.tasks == [] and smtp == []


def test_payee_request_task_flag_off_never_constructs_smtp(smtp):
    ctx = {"request_id": 1, "employee_id": "E1", "employee_name": "ก", "account_no": "1234567890",
           "account_name": "ก", "app_origin": "https://a.com"}
    pr._send_request_email(ctx)  # the task create_payee_request queues
    assert smtp == []
    assert "send_finance_email" in inspect.getsource(pr._send_request_email)
    assert "send_email(" not in inspect.getsource(pr)


def test_payee_request_task_flag_on_uses_finance_sender(monkeypatch, smtp):
    monkeypatch.setenv("FINANCE_EMAIL_ENABLED", "true")
    monkeypatch.setenv("FINANCE_SMTP_USER", "fin@x.com")
    monkeypatch.setenv("FINANCE_SMTP_PASSWORD", "pw")
    pr._send_request_email({"request_id": 1, "employee_id": "E1", "employee_name": "ก",
                            "account_no": "1234567890", "account_name": "ก", "app_origin": "https://a.com"})
    assert ("login", "fin@x.com", "pw") in smtp


def test_submit_and_approve_paths_flag_off_never_construct_smtp(smtp):
    """Every route event goes through queue_event, which is inert while off; the tasks list stays empty."""
    bg = BackgroundTasks()
    for event in (fm.SUBMITTED, fm.STEP_PENDING, fm.APPROVED, fm.REJECTED, fm.RETURNED, fm.RESUBMITTED,
                  fm.PAID, fm.SENT_BACK, fm.CLOSED):
        fm.queue_event(bg, Boom(), event, Boom(), remark="r")
    assert bg.tasks == [] and smtp == []


def test_routes_are_wired_to_queue_event():
    assert "fin_mail.queue_event(background_tasks, db, fin_mail.SUBMITTED" in inspect.getsource(fs.submit_form)
    approve = inspect.getsource(fa.approve_submission)
    assert "fin_mail.APPROVED" in approve and "fin_mail.STEP_PENDING" in approve
    assert "fin_mail.REJECTED" in inspect.getsource(fa.reject_submission)
    for fn, ev in ((ar.return_advance, "RETURNED"), (ar.resubmit_advance, "RESUBMITTED"), (ar.pay_advance, "PAID"),
                   (ar.send_back_advance, "SENT_BACK"), (ar.confirm_advance, "CLOSED")):
        assert f"mail.{ev}" in inspect.getsource(fn)


# ---------------------------- queue_event with the switch on ----------------------------

class _Q:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *a, **k):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class _Db:
    def __init__(self, rows):
        self.rows = rows

    def query(self, *a):
        return _Q(self.rows)


def test_queue_event_requester_event_on(monkeypatch, smtp):
    monkeypatch.setenv("FINANCE_EMAIL_ENABLED", "true")
    monkeypatch.setenv("FINANCE_SMTP_USER", "fin@x.com")
    monkeypatch.setenv("FINANCE_SMTP_PASSWORD", "pw")
    monkeypatch.setenv("FE_BASE_URL", "https://app.example.com")
    monkeypatch.setattr(fm.advance_repo, "request_values_by_submission", lambda db, ids: {1: {"amount": 1500}})
    monkeypatch.setattr(fm.advance_repo, "people_by_employee_id", lambda db, ids: {"E1": {"name": "สมชาย"}})
    sub = NS(id=1, form_id="ADV-2610-001", created_by="E1", current_approval_level=1)
    bg = BackgroundTasks()
    fm.queue_event(bg, _Db([NS(email="req@x.com")]), fm.REJECTED, sub, remark="ไม่ครบ")
    assert len(bg.tasks) == 1
    assert smtp == []  # queued, not sent yet
    # FastAPI runs the task after the response
    task = bg.tasks[0]
    task.func(*task.args, **task.kwargs)
    sent = next(c for c in smtp if c[0] == "sendmail")
    assert sent[2] == ["req@x.com"]


def test_queue_event_no_email_no_task(monkeypatch):
    monkeypatch.setenv("FINANCE_EMAIL_ENABLED", "true")
    monkeypatch.setattr(fm.advance_repo, "request_values_by_submission", lambda db, ids: {})
    monkeypatch.setattr(fm.advance_repo, "people_by_employee_id", lambda db, ids: {})
    bg = BackgroundTasks()
    fm.queue_event(bg, _Db([NS(email=None)]), fm.CLOSED, NS(id=1, form_id="F", created_by="E1"))
    assert bg.tasks == []


def test_queue_event_never_raises(monkeypatch):
    monkeypatch.setenv("FINANCE_EMAIL_ENABLED", "true")
    fm.queue_event(BackgroundTasks(), Boom(), fm.PAID, NS(id=1, form_id="F", created_by="E1"))


class _FailingDb:
    """A session whose lookups fail (as a broken query leaves it); rollback() records the call."""
    def __init__(self, rollback_raises=False):
        self.rollbacks, self.rollback_raises = 0, rollback_raises

    def query(self, *a, **k):
        raise RuntimeError("relation does not exist")

    def rollback(self):
        self.rollbacks += 1
        if self.rollback_raises:
            raise RuntimeError("connection gone")


def _enable(monkeypatch):
    monkeypatch.setenv("FINANCE_EMAIL_ENABLED", "true")
    monkeypatch.setenv("FINANCE_SMTP_USER", "fin@x.com")
    monkeypatch.setenv("FINANCE_SMTP_PASSWORD", "pw")


@pytest.mark.parametrize("event", [fm.PAID, fm.SUBMITTED])
@pytest.mark.parametrize("rollback_raises", [False, True])
def test_queue_event_failed_lookup_rolls_back(monkeypatch, event, rollback_raises):
    """The route already committed; a failed recipient lookup must end the failed transaction, or the request's
    next DB use raises PendingRollbackError (500)."""
    _enable(monkeypatch)
    db = _FailingDb(rollback_raises)
    bg = BackgroundTasks()
    fm.queue_event(bg, db, event, NS(id=1, form_id="F", created_by="E1", current_approval_level=1))  # no raise
    assert db.rollbacks >= 1 and bg.tasks == []


def test_queue_event_failed_step_line_rolls_back_and_still_queues(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(fm.advance_repo, "request_values_by_submission", lambda db, ids: {1: {"amount": 1500}})
    monkeypatch.setattr(fm.advance_repo, "people_by_employee_id", lambda db, ids: {})

    def broken(db):
        raise RuntimeError("statement failed")

    monkeypatch.setattr(approval_repo, "load_context", broken)
    monkeypatch.setattr(fm, "step_approver_emails", lambda db, sub: ["a@x.com"])
    db = _FailingDb()
    bg = BackgroundTasks()
    fm.queue_event(bg, db, fm.STEP_PENDING, NS(id=1, form_id="F", created_by="E1", current_approval_level=2))
    assert db.rollbacks == 1 and len(bg.tasks) == 1


# ---------------------------- renders ----------------------------

CTX = {"form_id": "ADV-2610-001", "requester_name": '<b>สมชาย</b> & "ก"', "amount": 15000, "remark": "<script>x</script>",
       "step": 1, "total_steps": 2, "step_label": "หัวหน้าระดับ 4 ขึ้นไป", "fe_base_url": "https://app.example.com",
       "amount_paid": 15000, "transfer_date": "2026-10-06", "clear_due_date": "2026-10-20"}


@pytest.mark.parametrize("event", list(fm._TITLES))
def test_every_event_renders_and_escapes(event):
    subject, html = fm.render_event_email(event, CTX)
    assert subject.startswith("[Finance Advance] ADV-2610-001")
    assert "\n" not in subject
    assert "<script>" not in html and "<b>" not in html
    assert "&lt;b&gt;" in html and "&amp;" in html
    assert "15,000.00" in html


def test_link_targets():
    _, approver = fm.render_event_email(fm.SUBMITTED, CTX)
    assert 'href="https://app.example.com/finance/approvals?doc=ADV-2610-001"' in approver
    _, requester = fm.render_event_email(fm.PAID, CTX)
    assert 'href="https://app.example.com/finance/advance/ADV-2610-001"' in requester


@pytest.mark.parametrize("origin", [None, "", "javascript:alert(1)", "https://evil.com/x", 'https://a.com"><x',
                                    "https://a.com/", "ftp://a.com", "https://a.com?x=1", "//a.com"])
def test_no_link_for_bad_origin(origin):
    for event in (fm.SUBMITTED, fm.CLOSED):
        _, html = fm.render_event_email(event, {**CTX, "fe_base_url": origin})
        assert "href=" not in html


@pytest.mark.parametrize("origin", ["https://app.example.com", "http://localhost:4000", "https://a-b.co"])
def test_link_for_good_origin(origin):
    _, html = fm.render_event_email(fm.RETURNED, {**CTX, "fe_base_url": origin})
    assert f'href="{origin}/finance/advance/ADV-2610-001"' in html


def test_form_id_cannot_break_out_of_link():
    _, html = fm.render_event_email(fm.SUBMITTED, {**CTX, "form_id": 'A"><script>1</script>'})
    assert "<script>" not in html


def test_event_specific_content():
    _, paid = fm.render_event_email(fm.PAID, CTX)
    assert "06/10/2026" in paid and "20/10/2026" in paid
    _, rej = fm.render_event_email(fm.REJECTED, CTX)
    assert "เหตุผล" in rej
    _, step = fm.render_event_email(fm.STEP_PENDING, CTX)
    assert "ขั้นที่ 1 จาก 2" in step
    _, closed = fm.render_event_email(fm.CLOSED, CTX)
    assert "เหตุผล" not in closed


# ---------------------------- recipients ----------------------------

def test_pick_recipients_dedupes_and_caps(caplog):
    emails = ["a@x.com", "A@x.com", None, "", " b@x.com "] + [f"u{i}@x.com" for i in range(30)]
    with caplog.at_level(logging.WARNING):
        out = fm.pick_recipients(emails)
    assert out[:2] == ["a@x.com", "b@x.com"] and len(out) == 20
    assert any("truncated" in r.message for r in caplog.records)
    assert fm.pick_recipients(["a@x.com"]) == ["a@x.com"]


def _person(eid, uid, level, dept, active=True):
    return {"employee_id": eid, "id": uid, "level": level, "department_id": dept, "active": active}


def test_step_approver_emails_selection(monkeypatch):
    people = {p["employee_id"]: p for p in (
        _person("R", 1, 3, 10),                  # requester: never a recipient
        _person("A", 2, 4, 10),                  # eligible
        _person("A2", 3, 6, 10),                 # eligible, higher level, same email as A -> deduped
        _person("OUT", 4, 5, 20),                # other department, not mapped
        _person("MAP", 5, 4, 20),                # other department but mapped to 10
        _person("OLD", 6, 7, 10, active=False),  # inactive
        _person("LOW", 7, 3, 10),                # level too low
        _person("EX", 8, 5, 10),                 # excluded: approved step 1 in this round
        _person("NOMAIL", 9, 4, 10),             # eligible, no email
        _person("BIG", 10, 9, 99),               # org-wide
    )}
    ctx = approval_repo.ApprovalContext(tiers=[], people=people, mappings={"MAP": {10}})
    steps = [{"step": 1, "required_level": 4, "label": "x"}, {"step": 2, "required_level": 4, "label": "y"}]
    monkeypatch.setattr(approval_repo, "load_context", lambda db: ctx)
    monkeypatch.setattr(approval_repo, "describe", lambda db, eid, amount, ctx=None: {"steps": steps})
    monkeypatch.setattr(approval_repo, "_amount_of", lambda db, sid: 15000)
    logs = [{"id": 1, "level_no": 1, "action": "APPROVED", "action_by": 8, "action_at": None, "remark": None}]
    monkeypatch.setattr(approval_repo, "current_round_logs", lambda db, sid: logs)
    users = [NS(id=2, email="a@x.com"), NS(id=3, email="A@X.com"), NS(id=4, email="out@x.com"),
             NS(id=5, email="map@x.com"), NS(id=1, email="r@x.com"), NS(id=8, email="ex@x.com"),
             NS(id=9, email=None), NS(id=10, email="big@x.com")]
    sub = NS(id=1, created_by="R", current_approval_level=2)
    assert fm.step_approver_emails(_Db(users), sub) == ["a@x.com", "map@x.com", "big@x.com"]
    sub1 = NS(id=1, created_by="R", current_approval_level=1)
    monkeypatch.setattr(approval_repo, "current_round_logs", lambda db, sid: [])
    out = fm.step_approver_emails(_Db(users), sub1)
    assert "ex@x.com" in out and "r@x.com" not in out and "out@x.com" not in out
