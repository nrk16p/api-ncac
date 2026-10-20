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


# ---- หน้าตาอีเมลอิงข้อความไลน์กลุ่มบัญชี (ผู้ใช้สั่ง 20/10/2026) ----

CTX = {"form_id": "ADV-2610-011", "requester_name": "จุฑารัตน์ จำนงค์สังข์", "department": "แผนกบัญชี",
       "amount": 5000, "purpose": "สำรองจ่ายค่าครูฝึก พจส.ใหม่", "use_date": "2026-10-21",
       "approver_name": "เจษฎา รองศักดิ์", "fe_base_url": "https://app.example.com"}


def test_approved_email_reads_like_the_line_message():
    subject, html = fm.render_event_email(fm.APPROVED, CTX)
    assert subject == "เบิกเงิน Advance · อนุมัติครบทุกขั้นแล้ว · ADV-2610-011"
    for text in ("อนุมัติครบทุกขั้นแล้ว", "จุฑารัตน์ จำนงค์สังข์ (แผนกบัญชี)", "ยอดเบิก", "5,000.00",
                 "สำรองจ่ายค่าครูฝึก พจส.ใหม่", "วันที่ใช้เงิน", "21/10/2026", "ผู้อนุมัติขั้นสุดท้าย",
                 "เจษฎา รองศักดิ์", "สถานะ", "รอตั้งเบิก", "/finance/advance/ADV-2610-011"):
        assert text in html, text


def _clear(**kw):
    return {**CTX, "amount_paid": 5000, "amount_actual": 4600, "settle_amount": 400, **kw}


def test_clear_email_waiting_for_the_leader():
    subject, html = fm.render_event_email(fm.CLEAR_SUBMITTED, _clear(pending=True, step=1, total_steps=1, clause="6.6"))
    assert subject == "เบิกเงิน Advance · ส่งเคลียร์เงินแล้ว · ADV-2610-011"
    for text in ("จ่ายไปแล้ว", "5,000.00", "ยอดใช้จริง", "4,600.00", "คืนบริษัท", "400.00",
                 "ขั้นที่ 1 จาก 1 (TOA ข้อ 6.6)", "รออนุมัติเคลียร์"):
        assert text in html, text
    assert "รอบัญชีตรวจ" not in html


def test_clear_email_straight_to_accounting():
    _subject, html = fm.render_event_email(fm.CLEAR_SUBMITTED, _clear(pending=False))
    assert "รอบัญชีตรวจ" in html
    assert "ขั้นอนุมัติ" not in html and "รออนุมัติเคลียร์" not in html


def test_clear_email_when_the_company_owes_the_requester():
    _subject, html = fm.render_event_email(fm.CLEAR_SUBMITTED, _clear(amount_paid=30000, amount_actual=31200,
                                                                      settle_amount=-1200, pending=False))
    assert "บริษัทจ่ายเพิ่ม" in html and "1,200.00" in html
    assert "คืนบริษัท" not in html


def test_clear_email_without_change_shows_no_settle_line():
    _subject, html = fm.render_event_email(fm.CLEAR_SUBMITTED, _clear(amount_actual=5000, settle_amount=0, pending=False))
    assert "คืนบริษัท" not in html and "บริษัทจ่ายเพิ่ม" not in html


def test_clear_submitted_goes_to_requester_and_ccs_accounting(monkeypatch, smtp):
    _fire(monkeypatch, fm.CLEAR_SUBMITTED, amount_paid=5000, amount_actual=4600, settle_amount=400, pending=True)
    assert smtp[0][0] == ["req@x.com", "accountbkk@menatransport.co.th"]


def test_routes_send_both_accounting_emails():
    import inspect
    from routes.finance import advance_routes as ar
    from routes.forms import form_approval_routes as fa
    clear_src = inspect.getsource(ar.clear_advance)
    assert "mail.CLEAR_SUBMITTED" in clear_src and "pending=pending_approval" in clear_src
    after_mail = inspect.getsource(fa.approve_submission).split("fin_mail.queue_event", 1)[1][:400]
    assert "approver_name=" in after_mail


# ---- อีเมลขออนุมัติ CC ผู้เบิกด้วย (ผู้ใช้สั่ง 20/10/2026) ----

def _fire_approver(monkeypatch, event):
    monkeypatch.setattr(fm.advance_repo, "request_values_by_submission", lambda db, ids: {1: {"amount": 4500}})
    monkeypatch.setattr(fm.advance_repo, "people_by_employee_id", lambda db, ids: {"E1": {"name": "กฤตบุญ"}})
    monkeypatch.setattr(fm, "step_approver_emails", lambda db, sub: ["boss1@x.com", "boss2@x.com"])
    monkeypatch.setattr(fm.approval_repo, "ensure_people", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("skip")))
    bg = BackgroundTasks()
    fm.queue_event(bg, _Db([NS(email="req@x.com")]), event, NS(id=1, form_id="ADV-2610-008", created_by="E1",
                                                               current_approval_level=1))
    for t in bg.tasks:
        t.func(*t.args, **t.kwargs)


@pytest.mark.parametrize("event", [fm.SUBMITTED, fm.STEP_PENDING, fm.RESUBMITTED])
def test_approval_request_ccs_the_requester_once(monkeypatch, smtp, event):
    _fire_approver(monkeypatch, event)
    assert [to for to, _ in smtp] == [["boss1@x.com", "req@x.com"], ["boss2@x.com"]]
    assert "Cc: req@x.com" in smtp[0][1] and "Cc:" not in smtp[1][1]


def test_requester_who_is_also_an_approver_is_not_doubled(monkeypatch, smtp):
    monkeypatch.setattr(fm.advance_repo, "request_values_by_submission", lambda db, ids: {1: {"amount": 4500}})
    monkeypatch.setattr(fm.advance_repo, "people_by_employee_id", lambda db, ids: {"E1": {"name": "x"}})
    monkeypatch.setattr(fm, "step_approver_emails", lambda db, sub: ["req@x.com"])
    bg = BackgroundTasks()
    fm.queue_event(bg, _Db([NS(email="req@x.com")]), fm.SUBMITTED, NS(id=1, form_id="F", created_by="E1",
                                                                      current_approval_level=1))
    for t in bg.tasks:
        t.func(*t.args, **t.kwargs)
    assert [to for to, _ in smtp] == [["req@x.com"]]
