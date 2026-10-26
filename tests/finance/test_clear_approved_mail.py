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


LETTER_CTX = {**CTX, "clear_due_date": "2026-10-27"}


def test_letter_subject_has_full_name():
    subject, _html = fm.render_event_email(fm.CLEAR_APPROVED, LETTER_CTX)
    assert subject == "การเคลียร์เงิน Advance ผ่านการอนุมัติครบทุกขั้นแล้ว กฤตบุญ แหลงเงิน"


def test_letter_body_follows_accounting_wording():
    """ถ้อยคำฉบับ 21/10/2026 แบ่ง 2 ช่วงวันที่ + ผู้ติดต่อตามศูนย์ (แก้แค่คำผิด)"""
    _s, html = fm.render_event_email(fm.CLEAR_APPROVED, LETTER_CTX)
    for text in ("เรียน คุณกฤตบุญ,",
                 "อีเมลฉบับนี้ส่งมาเพื่อแจ้งการเคลียร์เงินยืมทดรองจ่าย หลังจากผ่านการอนุมัติครบทุกขั้นแล้ว",
                 "จากใบคำขอเบิกเงิน ADV-2610-009 จำนวน 4,500.00 บาท ซึ่งจะครบกำหนดชำระในวันที่ 27/10/2026",
                 "โดยมีขั้นตอนการเคลียร์เงินยืมทดรอง หลังผ่านการอนุมัติครบทุกขั้นแล้ว แบ่งเป็น 2 ช่วงเวลา ดังนี้",
                 "1. ช่วงการเคลียร์ในระบบ สำหรับวันที่ 1-26 ของทุกเดือน",
                 "1.1 ส่งเอกสารตัวจริงให้ทางบัญชีทันทีหลังจากที่มีการเคลียร์เงินทดรองในระบบเรียบร้อยแล้ว โดยเอกสารหลัก ๆ คือ",
                 "1.1.1 พิมพ์แบบฟอร์มใบสรุปการเคลียร์เงินในระบบขอเบิกจ่ายเงินทดรอง",
                 "1.1.2 ใบเสร็จรับเงินและเอกสารประกอบเพื่อเคลียร์ค่าใช้จ่าย (หลักฐานการโอนเงินคืนบริษัท <i>ถ้ามีการคืนเงิน</i>)",
                 "2. ช่วงการเคลียร์ในระบบ สำหรับวันที่ 25-31 ของทุกเดือน",
                 "2.1 สแกนเอกสารการเคลียร์ให้ทางบัญชีก่อน โดยผ่านเมล accountbkk@menatransport.co.th ทันทีที่มีการอนุมัติเคลียร์เงินทดรองครบทุกขั้นในระบบแล้ว",
                 "2.2 ส่งเอกสารตัวจริงให้ทางบัญชีทันทีหลังจากที่มีการเคลียร์เงินทดรองในระบบเรียบร้อยแล้ว โดยเอกสารหลัก ๆ คือ",
                 "2.2.1 พิมพ์แบบฟอร์มใบสรุปการเคลียร์เงินในระบบขอเบิกจ่ายเงินทดรอง",
                 "2.2.2 ใบเสร็จรับเงินและเอกสารประกอบเพื่อเคลียร์ค่าใช้จ่าย",
                 "หากคุณกฤตบุญ มีข้อติดขัด หรือต้องการสอบถามรายละเอียดเพิ่มเติม สามารถติดต่อฝ่ายบัญชีได้โดยตรงทางอีเมล accountbkk@menatransport.co.th",
                 "ศูนย์ลาดกระบัง ขอนแก่น: ติดต่อคุณน้อง",
                 "ศูนย์สระบุรี บางปะกง ระยอง กรุงเทพ: ติดต่อคุณเบล",
                 "ขอขอบคุณสำหรับความร่วมมือค่ะ",
                 "/finance/advance/ADV-2610-009"):
        assert text in html, text
    for typo in ("ขอบเบิก", "1.2.2", "*ถ้ามีคืนเงิน*", "ตามอีเมล"):
        assert typo not in html, typo


def test_letter_contact_stays_accountbkk_even_when_cc_is_redirected(monkeypatch):
    monkeypatch.setenv("FINANCE_ACCOUNT_EMAIL", "uat-box@x.com")
    _s, html = fm.render_event_email(fm.CLEAR_APPROVED, LETTER_CTX)
    assert "accountbkk@menatransport.co.th" in html and "uat-box@x.com" not in html


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

    adv = NS(form_id="ADV-2610-009", clear_approval_level=1, amount_paid=4500, amount_actual=4300, clear_due_date="2026-10-27",
             settle_amount=200, fin_status=None, clear_approved_clause=None)
    sub = NS(id=1, form_id="ADV-2610-009", created_by="E1")
    monkeypatch.setattr(r, "_load_for_update", lambda db, fid: (sub, adv, r.logic.CLEARING_APPROVAL))
    monkeypatch.setattr(r, "_clear_plan", lambda db, s, amt: ({"clause": "6.6", "steps": [1, 2]}, NS(people={}, mappings={})))
    monkeypatch.setattr(r, "_clear_round_approvers", lambda db, fid: set())
    monkeypatch.setattr(r, "_clear_round_steps", lambda db, fid: set())
    monkeypatch.setattr(r.clr, "can_approve", lambda *a: True)
    monkeypatch.setattr(r.clr, "check_approve", lambda *a: None)
    monkeypatch.setattr(r, "_clear_approver_level", lambda *a: (3, None))
    monkeypatch.setattr(r.approval_rules, "skipped_steps", lambda steps, step, level, who=None, done=(): [])
    monkeypatch.setattr(r.approval_rules, "next_after_approval",
                        lambda steps, step, level, who=None, done=(): ("approved", None) if next_step is None else ("step", next_step))
    monkeypatch.setattr(r, "_clear_log", lambda *a: None)
    monkeypatch.setattr(r, "_commit_and_return", lambda db, fid: {"form_id": fid})
    monkeypatch.setattr(r.repo, "people_by_employee_id", lambda db, ids: {"A9": {"name": "ณรงค์กร อัตถะ"}})
    monkeypatch.setattr(r, "_clear_approver_emails", lambda db, s, info, ctx, step: [f"step{step}@x.com"])
    calls = []
    monkeypatch.setattr(r.mail, "queue_event", lambda bg, db, event, s, **kw: calls.append((event, kw)))

    out = r.clear_approve("ADV-2610-009", NS(action_by="A9", remark=None), BackgroundTasks(), db=None)
    assert out == {"form_id": "ADV-2610-009"}
    if expect_mail:
        assert calls == [(fm.CLEAR_APPROVED, {"approver_name": "ณรงค์กร อัตถะ", "clear_due_date": "2026-10-27",
                                               "amount_paid": 4500, "amount_actual": 4300, "settle_amount": 200})]
        assert adv.fin_status == r.logic.FIN_CLEARING_SUBMITTED
    else:  # ผ่านขั้น 1 แล้ว: แจ้งหัวหน้าขั้น 2 ไม่ใช่ผู้เบิก
        assert [c[0] for c in calls] == [fm.CLEAR_PENDING]
        assert calls[0][1]["approver_emails"] == ["step2@x.com"] and calls[0][1]["step"] == 2


# ---- รอท่านอนุมัติการเคลียร์: ถึงหัวหน้า CC ผู้เบิก (ผู้ใช้สั่ง 20/10/2026) ----

def test_clear_pending_email_for_heads():
    subject, html = fm.render_event_email(fm.CLEAR_PENDING, {**CTX, "step": 1, "total_steps": 2, "clause": "6.6"})
    assert subject == "เบิกเงิน Advance · รอท่านอนุมัติการเคลียร์ · ADV-2610-009"
    for text in ("รอท่านอนุมัติการเคลียร์", "จ่ายไปแล้ว", "4,500.00", "ยอดใช้จริง", "4,300.00", "คืนบริษัท",
                 "ขั้นที่ 1 จาก 2 (TOA ข้อ 6.6)", "รออนุมัติเคลียร์",
                 "/finance/approvals?tab=clear&amp;doc=ADV-2610-009"):
        assert text in html, text
    assert "Expense Claim Form" not in html, "ขั้นตอนพิมพ์เอกสารเป็นของผู้เบิก ไม่ใช่หัวหน้า"


def test_clear_pending_goes_to_given_heads_and_ccs_requester(monkeypatch):
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
    fm.queue_event(bg, NS(query=lambda *a: _Q()), fm.CLEAR_PENDING, NS(id=1, form_id="ADV-2610-009", created_by="E1"),
                   approver_emails=["lead@x.com", "mgr@x.com"], step=1, total_steps=1)
    for t in bg.tasks:
        t.func(*t.args, **t.kwargs)
    assert sent == [["lead@x.com", "req@x.com"], ["mgr@x.com"]]


def test_clear_pending_with_no_heads_sends_nothing(monkeypatch):
    monkeypatch.setenv("FINANCE_EMAIL_ENABLED", "true")
    monkeypatch.setattr(fm.advance_repo, "request_values_by_submission", lambda db, ids: {})
    monkeypatch.setattr(fm.advance_repo, "people_by_employee_id", lambda db, ids: {})
    bg = BackgroundTasks()
    fm.queue_event(bg, None, fm.CLEAR_PENDING, NS(id=1, form_id="ADV-2610-009", created_by="E1"), approver_emails=[])
    assert bg.tasks == []
