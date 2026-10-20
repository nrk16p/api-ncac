"""อนุมัติเคลียร์แล้ว อีเมลถึงผู้เบิก CC บัญชี บอกให้พิมพ์เอกสาร 2 ส่วน แล้วส่งตัวจริงให้บัญชี (ผู้ใช้สั่ง 20/10/2026)."""
import os
from types import SimpleNamespace as NS

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

import smtplib  # noqa: E402

import pytest  # noqa: E402
from fastapi import BackgroundTasks  # noqa: E402

from services.finance import finance_mail as fm  # noqa: E402

CTX = {"form_id": "ADV-2610-009", "requester_name": "กฤตบุญ แหลงเงิน", "department": "IT",
       "amount_paid": 4500, "amount_actual": 4300, "settle_amount": 200,
       "approver_name": "ณรงค์กร อัตถะ", "fe_base_url": "https://app.example.com"}
STEPS = ("พิมพ์เอกสาร", "Part 1", "Cash Advance Request", "ใบคำขอเบิกเงินล่วงหน้า",
         "Part 2", "Expense Claim Form", "ใบขอเบิกค่าใช้จ่าย/เคลียร์เบิกล่วงหน้า", "ตัวจริง", "ฝ่ายบัญชี")


def test_subject_and_rows():
    subject, html = fm.render_event_email(fm.CLEAR_APPROVED, CTX)
    assert subject == "เบิกเงิน Advance · อนุมัติเคลียร์แล้ว · ADV-2610-009"
    for text in ("อนุมัติเคลียร์แล้ว", "กฤตบุญ แหลงเงิน (IT)", "จ่ายไปแล้ว", "4,500.00", "ยอดใช้จริง", "4,300.00",
                 "คืนบริษัท", "200.00", "ผู้อนุมัติเคลียร์", "ณรงค์กร อัตถะ", "รอบัญชีตรวจ",
                 "/finance/advance/ADV-2610-009"):
        assert text in html, text


def test_tells_the_requester_to_print_both_parts_and_send_originals():
    _s, html = fm.render_event_email(fm.CLEAR_APPROVED, CTX)
    for text in STEPS:
        assert text in html, text


def test_clear_going_straight_to_accounting_also_carries_the_steps():
    _s, html = fm.render_event_email(fm.CLEAR_SUBMITTED, {**CTX, "pending": False})
    for text in STEPS:
        assert text in html, text


def test_clear_still_waiting_for_the_leader_has_no_print_steps():
    _s, html = fm.render_event_email(fm.CLEAR_SUBMITTED, {**CTX, "pending": True, "step": 1, "total_steps": 1})
    assert "Expense Claim Form" not in html


def test_goes_to_the_requester_and_ccs_accounting(monkeypatch):
    for k in ("FINANCE_ACCOUNT_EMAIL", "FINANCE_EMAIL_TEST_TO"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FINANCE_EMAIL_ENABLED", "true")
    monkeypatch.setenv("FINANCE_SMTP_USER", "itops@x.com")
    monkeypatch.setenv("FINANCE_SMTP_PASSWORD", "pw")
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def starttls(self): pass
        def login(self, u, p): pass
        def sendmail(self, frm, to, body): sent.append(to)
        def quit(self): pass

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(fm.advance_repo, "request_values_by_submission", lambda db, ids: {1: {"amount": 4500}})
    monkeypatch.setattr(fm.advance_repo, "people_by_employee_id", lambda db, ids: {"E1": {"name": "กฤตบุญ"}})

    class _Q:
        def filter(self, *a): return self
        def first(self): return NS(email="req@x.com")

    bg = BackgroundTasks()
    fm.queue_event(bg, NS(query=lambda *a: _Q()), fm.CLEAR_APPROVED, NS(id=1, form_id="ADV-2610-009", created_by="E1"))
    for t in bg.tasks:
        t.func(*t.args, **t.kwargs)
    assert sent == [["req@x.com", "accountbkk@menatransport.co.th"]]


# ---- route: ส่งเฉพาะตอนอนุมัติขั้นสุดท้าย ----

@pytest.mark.parametrize("next_step,expect_mail", [(None, True), (2, False)])
def test_route_sends_only_on_the_last_clearing_step(monkeypatch, next_step, expect_mail):
    from routes.finance import advance_routes as r

    adv = NS(form_id="ADV-2610-009", clear_approval_level=1, amount_paid=4500, amount_actual=4300,
             settle_amount=200, fin_status=None, clear_approved_clause=None)
    sub = NS(id=1, form_id="ADV-2610-009", created_by="E1")
    monkeypatch.setattr(r, "_load_for_update", lambda db, fid: (sub, adv, r.logic.CLEARING_APPROVAL))
    monkeypatch.setattr(r, "_clear_plan", lambda db, s, amt: ({"clause": "6.6", "steps": [1, 2]}, NS(people={}, mappings={})))
    monkeypatch.setattr(r, "_clear_round_approvers", lambda db, fid: set())
    monkeypatch.setattr(r.clr, "can_approve", lambda *a: True)
    monkeypatch.setattr(r.clr, "check_approve", lambda *a: None)
    monkeypatch.setattr(r.clr, "next_step", lambda info, step: next_step)
    monkeypatch.setattr(r, "_clear_log", lambda *a: None)
    monkeypatch.setattr(r, "_commit_and_return", lambda db, fid: {"form_id": fid})
    monkeypatch.setattr(r.repo, "people_by_employee_id", lambda db, ids: {"A9": {"name": "ณรงค์กร อัตถะ"}})
    calls = []
    monkeypatch.setattr(r.mail, "queue_event", lambda bg, db, event, s, **kw: calls.append((event, kw)))

    out = r.clear_approve("ADV-2610-009", NS(action_by="A9", remark=None), BackgroundTasks(), db=None)
    assert out == {"form_id": "ADV-2610-009"}
    if expect_mail:
        assert calls == [(fm.CLEAR_APPROVED, {"approver_name": "ณรงค์กร อัตถะ", "amount_paid": 4500,
                                               "amount_actual": 4300, "settle_amount": 200})]
        assert adv.fin_status == r.logic.FIN_CLEARING_SUBMITTED
    else:
        assert calls == []
