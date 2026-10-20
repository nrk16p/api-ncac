"""อนุมัติครบแล้ว อีเมลแจ้งผู้เบิก CC กล่องบัญชีด้วย ให้บัญชีรู้ว่ามีใบรอตั้งเบิก (ผู้ใช้สั่ง 20/10/2026)."""
import os
from types import SimpleNamespace as NS

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

import smtplib  # noqa: E402

import pytest  # noqa: E402
from fastapi import BackgroundTasks  # noqa: E402

from services.finance import finance_mail as fm  # noqa: E402

ENV = ("FINANCE_EMAIL_ENABLED", "FINANCE_SMTP_USER", "FINANCE_SMTP_PASSWORD", "FE_BASE_URL", "FINANCE_ACCOUNT_EMAIL")


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FINANCE_EMAIL_ENABLED", "true")
    monkeypatch.setenv("FINANCE_SMTP_USER", "itops@x.com")
    monkeypatch.setenv("FINANCE_SMTP_PASSWORD", "pw")


@pytest.fixture
def smtp(monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def starttls(self): pass
        def login(self, u, p): pass
        def sendmail(self, frm, to, body): sent.append((to, body))
        def quit(self): pass

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return sent


class _Q:
    def __init__(self, rows): self.rows = rows
    def filter(self, *a): return self
    def first(self): return self.rows[0] if self.rows else None


class _Db:
    def __init__(self, rows): self.rows = rows
    def query(self, *a): return _Q(self.rows)


def _fire(monkeypatch, event, **extra):
    monkeypatch.setattr(fm.advance_repo, "request_values_by_submission", lambda db, ids: {1: {"amount": 5000}})
    monkeypatch.setattr(fm.advance_repo, "people_by_employee_id", lambda db, ids: {"E1": {"name": "จุฑารัตน์"}})
    bg = BackgroundTasks()
    fm.queue_event(bg, _Db([NS(email="req@x.com")]), event, NS(id=1, form_id="ADV-2610-011", created_by="E1"), **extra)
    for t in bg.tasks:
        t.func(*t.args, **t.kwargs)


def test_approved_email_ccs_the_account_mailbox(monkeypatch, smtp):
    _fire(monkeypatch, fm.APPROVED)
    assert len(smtp) == 1
    to, body = smtp[0]
    assert to == ["req@x.com", "accountbkk@menatransport.co.th"]
    assert "To: req@x.com" in body and "Cc: accountbkk@menatransport.co.th" in body


def test_account_mailbox_comes_from_env(monkeypatch, smtp):
    monkeypatch.setenv("FINANCE_ACCOUNT_EMAIL", "uat-box@x.com")
    _fire(monkeypatch, fm.APPROVED)
    assert smtp[0][0] == ["req@x.com", "uat-box@x.com"]


@pytest.mark.parametrize("event", [fm.REJECTED, fm.RETURNED, fm.PAID, fm.SENT_BACK, fm.CLOSED])
def test_other_requester_emails_have_no_cc(monkeypatch, smtp, event):
    _fire(monkeypatch, event, remark="x")
    assert smtp[0][0] == ["req@x.com"]
    assert "Cc:" not in smtp[0][1]


def test_cc_goes_out_once_even_with_many_recipients(smtp):
    fm.deliver_finance_email(["a@x.com", "b@x.com"], "s", "<p>h</p>", cc=["acc@x.com"])
    envelopes = [to for to, _ in smtp]
    assert envelopes == [["a@x.com", "acc@x.com"], ["b@x.com"]]


def test_cc_that_is_already_a_recipient_is_not_doubled(smtp):
    fm.deliver_finance_email(["acc@x.com"], "s", "<p>h</p>", cc=["ACC@x.com"])
    assert [to for to, _ in smtp] == [["acc@x.com"]]


def test_cc_moves_to_the_next_message_when_the_first_fails(monkeypatch):
    sent = []

    class FlakySMTP:
        def __init__(self, *a, **k): pass
        def starttls(self): pass
        def login(self, u, p): pass
        def sendmail(self, frm, to, body):
            if to[0] == "a@x.com":
                raise smtplib.SMTPRecipientsRefused({})
            sent.append(to)
        def quit(self): pass

    monkeypatch.setattr(smtplib, "SMTP", FlakySMTP)
    fm.deliver_finance_email(["a@x.com", "b@x.com"], "s", "<p>h</p>", cc=["acc@x.com"])
    assert sent == [["b@x.com", "acc@x.com"]]


def test_switch_off_sends_nothing_even_with_cc(monkeypatch, smtp):
    monkeypatch.setenv("FINANCE_EMAIL_ENABLED", "false")
    assert fm.deliver_finance_email(["a@x.com"], "s", "<p>h</p>", cc=["acc@x.com"]) == 0
    assert smtp == []


def test_approved_email_tells_accounting_it_waits_for_voucher():
    _subject, html = fm.render_event_email(fm.APPROVED, {"form_id": "ADV-2610-011", "amount": 5000})
    assert "รอฝ่ายบัญชีตั้งเบิก" in html
