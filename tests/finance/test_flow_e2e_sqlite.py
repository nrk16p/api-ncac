# -*- coding: utf-8 -*-
"""End-to-end flow test of เบิกเงิน Advance through the REAL FastAPI routes.

* Runs on a throwaway in-memory SQLite DB (never the prod Postgres): the global engine in database.py is pointed at
  a dead address and `get_db` is overridden with a session bound to SQLite.
* Every finance email is captured by a fake smtplib.SMTP — nothing leaves the machine.
* Only the routers the flow needs are mounted (main.py is NOT imported).

Run alone:  pytest tests/finance/test_flow_e2e_sqlite.py -q -p no:cacheprovider -s   (-s prints the step trace)

SQLite-only workarounds (test harness, not app behaviour):
* JSONB is compiled as JSON.
* Only the tables the flow touches are created, from a COPY of the metadata, with the fin_status CHECK widened to
  the prod constraint (scripts/migrations/2026-10-19_finance_clear_approval.sql). The model itself still lacks
  CLEAR_PENDING — see test_model_check_constraint_matches_prod_sql (xfail).
* SELECT … FOR UPDATE is a no-op on SQLite (single-threaded test, so no effect on results).
"""
import os
import sys

_DEAD_DB = "postgresql://nobody:nobody@127.0.0.1:1/none"
# must happen before database.py is imported anywhere (load_dotenv() never overrides an existing variable)
os.environ["DATABASE_URL"] = _DEAD_DB
os.environ["DATALAKE_URL"] = ""

import email  # noqa: E402
import smtplib  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from email.header import decode_header, make_header  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import CheckConstraint, MetaData, create_engine  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):
    return "JSON"


import database  # noqa: E402
import models  # noqa: E402,F401  (registers every model on Base.metadata)
from models.finance_model import (  # noqa: E402
    FinAccount, FinAdvance, FinApprovalTier, FinPayeeAccount, FinPermission,
)
from models.master_model import FormMaster, FormQuestion  # noqa: E402
from models.user_model import Department, Position, PositionLevel, Site, User  # noqa: E402
from routes.finance import advance_routes, payee_routes  # noqa: E402
from routes.forms import form_approval_routes, form_submission_routes  # noqa: E402
from services.finance import approval_repo, permission_repo  # noqa: E402

BKK = ZoneInfo("Asia/Bangkok")
ACCOUNT_BOX = "accountbkk@menatransport.co.th"
FE = "https://app.test"

TABLES = [
    "position_levels", "positions", "departments", "sites", "users",
    "form_masters", "form_questions", "form_question_options", "form_submissions", "form_submission_values",
    "form_sequences", "form_approval_logs", "form_approval_rules", "form_submission_logs",
    "form_approver_departments",
    "fin_accounts", "fin_advances", "fin_advance_logs", "fin_approval_tiers", "fin_advance_clear_items",
    "fin_payee_account_requests", "fin_payee_accounts", "fin_payee_account_logs", "fin_permissions",
    "fin_clear_approval_logs",
]
PROD_FIN_STATUS_CHECK = ("fin_status IS NULL OR fin_status IN ('VOUCHERED','VOUCHER_REJECTED','RETURNED',"
                         "'RESUBMITTED','PAID','CLEAR_PENDING','CLEARING_SUBMITTED','SENT_BACK','CLOSED')")

# ---------------------------------------------------------------- org
OPS, IT, EXECS, FIN_DEPT, ACC_DEPT = 11, 12, 99, 4, 6
# employee_id: (users.id, first, last, level, dept, email)
PEOPLE = {
    "E100": (1, "สมชาย", "ใจดี", 2, OPS, "req@test.local"),        # requester
    "E200": (2, "หัวหน้า", "ระดับสาม", 3, OPS, "lead@test.local"),
    "E210": (3, "ผช", "ผู้จัดการ", 4, OPS, "asm@test.local"),
    "E300": (4, "ผู้จัดการ", "ระดับห้า", 5, OPS, "mgr@test.local"),
    "E400": (5, "ผอ", "ระดับแปด", 8, OPS, "exec@test.local"),
    "E500": (6, "รอง", "ซีอีโอ", 9, EXECS, "dceo@test.local"),
    "670108": (7, "ซูเปอร์", "แอดมิน", 4, IT, "super@test.local"),
    "F001": (8, "ตั้งเบิก", "บัญชี", 3, ACC_DEPT, "voucher@test.local"),
    "F002": (9, "จ่ายเงิน", "การเงิน", 3, FIN_DEPT, "pay@test.local"),
    "F003": (10, "ตรวจเคลียร์", "บัญชี", 3, ACC_DEPT, "review@test.local"),
    "F004": (11, "ปิดรายการ", "บัญชี", 3, ACC_DEPT, "close@test.local"),
    "F005": (12, "ไม่มีสิทธิ์", "บัญชี", 3, ACC_DEPT, "noperm@test.local"),
}
REQ, LEAD, ASM, MGR, EXEC, DCEO, SUPER = "E100", "E200", "E210", "E300", "E400", "E500", "670108"
FV, FP, FR, FC, FX = "F001", "F002", "F003", "F004", "F005"
GRANTS = [(FV, "voucher"), (FP, "pay"), (FV, "return"), (FP, "return"), (FR, "clearing_review"),
          (FC, "close"), (FR, "payee_admin"), (FR, "overdue_remind")]
TIERS = [("6.7", 2000, 2, "Asst. Sup", 1), ("6.6", 5000, 3, "Sup / Asst.M", 2), ("6.5", 20000, 5, "MGR", 3),
         ("6.4", 100000, 8, "CL", 4), ("6.3", 500000, 9, "DCEO", 5), ("6.2", 1000000, 9, "CEO", 6),
         ("6.1", None, 9, "ExC", 7)]
QUESTIONS = [("adv_purpose", "longtext", 1), ("adv_amount", "number", 2), ("adv_use_date", "datetime", 3),
             ("adv_cost_center", "dropdown", 4), ("adv_payee_type", "dropdown", 5), ("adv_bank", "dropdown", 6),
             ("adv_account_no", "text", 7), ("adv_account_name", "text", 8)]


def email_of(eid):
    return PEOPLE[eid][5]


def today():
    return datetime.now(BKK).date()


# ---------------------------------------------------------------- fixtures

@pytest.fixture(autouse=True)
def env(monkeypatch):
    for key in ("FINANCE_EMAIL_TEST_TO", "FINANCE_ACCOUNT_EMAIL", "FINANCE_EMPLOYEE_IDS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("FINANCE_EMAIL_ENABLED", "true")
    monkeypatch.setenv("FINANCE_SMTP_USER", "itops@test.local")
    monkeypatch.setenv("FINANCE_SMTP_PASSWORD", "x")
    monkeypatch.setenv("FE_BASE_URL", FE)
    monkeypatch.setenv("FINANCE_DEPARTMENT_IDS", "4,6")
    monkeypatch.setenv("ADVANCE_NOTIFY_ENABLED", "false")  # IT-style email/LINE stays off for ADV
    approval_repo.clear_context_cache()
    permission_repo.clear_grants_cache()
    yield
    approval_repo.clear_context_cache()
    permission_repo.clear_grants_cache()


@pytest.fixture
def outbox(monkeypatch):
    """Fake SMTP: every message is recorded, nothing is sent. Also traps the IT mailer / LINE (must stay unused)."""
    sent = []

    class FakeSMTP:
        def __init__(self, host=None, port=None, timeout=None):
            assert host == "smtp.gmail.com"

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def sendmail(self, frm, to, raw):
            sent.append(_parse(frm, to, raw))

        def quit(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    other = []
    monkeypatch.setattr(form_submission_routes, "send_email", lambda *a, **k: other.append(("it", a)))
    monkeypatch.setattr(form_submission_routes, "send_line_message", lambda *a, **k: other.append(("line", a)))
    monkeypatch.setattr(form_approval_routes, "send_email", lambda *a, **k: other.append(("it", a)))
    box = Outbox(sent)
    yield box
    assert other == [], f"IT email / LINE must not fire for ADV: {other}"


@pytest.fixture
def api(monkeypatch, outbox):
    # belt and braces: the global (prod-config) engine must never be used by this test
    dead = create_engine(_DEAD_DB.replace("postgresql://", "postgresql+psycopg2://"), connect_args={"connect_timeout": 1})
    monkeypatch.setattr(database, "engine", dead)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=dead))

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    md = MetaData()
    for name in TABLES:
        database.Base.metadata.tables[name].to_metadata(md)
    fin = md.tables["fin_advances"]
    for c in list(fin.constraints):
        if getattr(c, "name", None) == "ck_fin_advances_fin_status":
            fin.constraints.discard(c)
    fin.append_constraint(CheckConstraint(PROD_FIN_STATUS_CHECK, name="ck_fin_advances_fin_status"))
    md.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    _seed(Session())

    app = FastAPI()
    app.include_router(advance_routes.router)
    app.include_router(payee_routes.router)
    app.include_router(form_approval_routes.router)
    app.include_router(form_submission_routes.router)

    def _get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[database.get_db] = _get_db
    client = TestClient(app, raise_server_exceptions=False)  # no `with`: startup events stay off
    flow = Flow(client, outbox, Session)
    yield flow
    engine.dispose()


def _seed(db):
    for lvl in range(1, 10):
        db.add(PositionLevel(position_level_id=lvl, level_name=f"L{lvl}"))
        db.add(Position(position_id=lvl, position_name_th=f"ตำแหน่ง {lvl}", position_name_en=f"P{lvl}",
                        position_level_id=lvl))
    for dept_id, name in ((OPS, "ปฏิบัติการ"), (IT, "ไอที"), (EXECS, "ผู้บริหาร"), (FIN_DEPT, "การเงิน"),
                          (ACC_DEPT, "บัญชี")):
        db.add(Department(department_id=dept_id, department_name_th=name, department_name_en=name))
    db.add(Site(site_id=1, site_code="BKK", site_name_th="สำนักงานใหญ่", site_name_en="HQ"))
    for eid, (uid, first, last, lvl, dept, mail) in PEOPLE.items():
        db.add(User(id=uid, username=mail.split("@")[0], password_hash="x", firstname=first, lastname=last,
                    employee_id=eid, department_id=dept, site_id=1, position_id=lvl, email=mail,
                    employee_status="Active"))
    for eid, perm in GRANTS:
        db.add(FinPermission(employee_id=eid, permission=perm, granted_by="TEST"))
    for clause, cap, lvl, label, order in TIERS:
        db.add(FinApprovalTier(clause=clause, amount_max=cap, min_level=lvl, approver_label=label, sort_order=order))
    db.add(FinAccount(acc_code="110101", acc_name="เงินทดรองจ่าย", is_active=True))
    form = FormMaster(id=1, form_type="Advance", form_code="ADV", form_name="เบิกเงิน Advance", form_status="Active",
                      need_approval=True, version=1, is_latest=True)
    db.add(form)
    for qid, (name, qtype, order) in enumerate(QUESTIONS, start=1):
        db.add(FormQuestion(id=qid, form_master_id=1, question_name=name, question_label=name, question_type=qtype,
                            is_required=True, sort_order=order))
    db.add(FinPayeeAccount(employee_id=REQ, bank="KBANK", account_no="1234567890", account_name="สมชาย ใจดี",
                           status="ACTIVE", created_by="TEST"))
    db.commit()
    db.close()


# ---------------------------------------------------------------- helpers

def _parse(frm, to, raw):
    msg = email.message_from_string(raw)
    html = msg.get_payload()[0].get_payload(decode=True).decode("utf-8")
    cc = [c.strip() for c in (msg["Cc"] or "").split(",") if c.strip()]
    return {"from": frm, "envelope": list(to), "to": msg["To"], "cc": cc,
            "subject": str(make_header(decode_header(msg["Subject"]))), "html": html}


class Outbox:
    def __init__(self, sent):
        self.sent = sent

    def take(self):
        out, self.sent[:] = list(self.sent), []
        return out


def group(mails):
    """subject → {"to": sorted To addresses, "cc": sorted CC addresses (union over that subject's messages)}."""
    out = {}
    for m in mails:
        g = out.setdefault(m["subject"], {"to": set(), "cc": set(), "html": []})
        g["to"].add(m["to"])
        g["cc"].update(m["cc"])
        g["html"].append(m["html"])
    return {s: {"to": sorted(g["to"]), "cc": sorted(g["cc"]), "html": g["html"]} for s, g in out.items()}


def legacy(form_id, headline):
    return f"[Finance Advance] {form_id} — {headline}"


def summary(form_id, headline):
    return f"เบิกเงิน Advance · {headline} · {form_id}"


TRACE = []


class Flow:
    def __init__(self, client, outbox, Session):
        self.c, self.outbox, self.Session = client, outbox, Session

    # -- request side
    def submit(self, amount, who=REQ, expect=200):
        qid = {name: i for i, (name, _t, _o) in enumerate(QUESTIONS, start=1)}
        use = datetime.combine(today() + timedelta(days=3), datetime.min.time()).isoformat()
        values = [
            {"question_id": qid["adv_purpose"], "value_text": "ค่าน้ำมันเดินทางไปไซต์"},
            {"question_id": qid["adv_amount"], "value_number": amount},
            {"question_id": qid["adv_use_date"], "value_date": use},
            {"question_id": qid["adv_cost_center"], "value_text": "ศลบ"},
            {"question_id": qid["adv_payee_type"], "value_text": "SELF"},
            {"question_id": qid["adv_bank"], "value_text": "BBL"},          # forged; SELF master must win
            {"question_id": qid["adv_account_no"], "value_text": "0000000000"},
            {"question_id": qid["adv_account_name"], "value_text": "ใครก็ไม่รู้"},
        ]
        r = self.c.post("/forms/submit", json={"form_code": "ADV", "created_by": who, "values": values})
        assert r.status_code == expect, r.text
        return r.json()["form_id"] if expect == 200 else r

    def approve(self, form_id, who, expect=200):
        r = self.c.post(f"/forms/{form_id}/approve", params={"employee_id": who})
        assert r.status_code == expect, r.text
        return r.json()

    def reject(self, form_id, who, remark="ไม่จำเป็น", expect=200):
        r = self.c.post(f"/forms/{form_id}/reject", params={"employee_id": who, "remark": remark})
        assert r.status_code == expect, r.text
        return r.json()

    # -- finance side
    def put(self, form_id, action, body, expect=200):
        r = self.c.put(f"/finance/advances/{form_id}/{action}", json=body)
        assert r.status_code == expect, f"{action}: {r.status_code} {r.text}"
        return r.json()

    def voucher(self, form_id, who=FV, expect=200):
        return self.put(form_id, "voucher", {"action_by": who, "voucher_no": "PV-1",
                                             "voucher_date": today().isoformat()}, expect)

    def pay(self, form_id, amount, who=FP, expect=200):
        return self.put(form_id, "pay", {"action_by": who, "acc_code": "110101", "payment_doc_no": "PAY-1",
                                         "amount_paid": str(amount), "transfer_date": today().isoformat()}, expect)

    def clear(self, form_id, lines, settle_date=True, who=REQ, expect=200):
        items = [{"expense_date": today().isoformat(), "description": f"รายการ {i}", "amount_before_vat": str(a)}
                 for i, a in enumerate(lines, start=1)]
        body = {"action_by": who, "clear_date": today().isoformat(), "items": items}
        if settle_date:
            body["settle_date"] = today().isoformat()
        return self.put(form_id, "clear", body, expect)

    def detail(self, form_id):
        r = self.c.get(f"/finance/advances/{form_id}")
        assert r.status_code == 200, r.text
        return r.json()

    def status(self, form_id):
        return self.detail(form_id)["status"]

    def mails(self, label=None, form_id=None):
        got = group(self.outbox.take())
        if label:
            TRACE.append((label, self.status(form_id) if form_id else "-",
                          [(s, g["to"], g["cc"]) for s, g in got.items()]))
        return got

    def paid_advance(self, amount, approver=ASM):
        """submit → (one-click) approve → voucher → pay; outbox emptied."""
        fid = self.submit(amount)
        res = self.approve(fid, approver)
        if res["status"] != "Approved":
            res = self.approve(fid, MGR if approver != MGR else EXEC)
        assert res["status"] == "Approved"
        self.voucher(fid)
        self.pay(fid, amount)
        self.outbox.take()
        return fid


def assert_mails(got, expected):
    """expected: {subject: (to list, cc list)} — exact set of subjects, exact recipients."""
    simplified = {s: (g["to"], g["cc"]) for s, g in got.items()}
    assert simplified == {s: (sorted(to), sorted(cc)) for s, (to, cc) in expected.items()}


# ================================================================ 1. happy path (refund)

def test_happy_path_refund(api):
    a = api
    fid = a.submit(12000)
    assert fid.startswith("ADV-") and len(fid) == len("ADV-2610-001")
    assert a.status(fid) == "PENDING_APPROVAL"
    req = a.detail(fid)["request"]
    assert (req["bank"], req["account_no"], req["account_name"]) == ("KBANK", "1234567890", "สมชาย ใจดี")
    got = a.mails("1 submit 12,000", fid)
    # step 1 needs lv3; email capped at max(3, 5) → lv3/4/5 of the department, never lv8/lv9 or the superadmin
    assert_mails(got, {legacy(fid, "รอท่านอนุมัติ"): (
        [email_of(LEAD), email_of(ASM), email_of(MGR)], [email_of(REQ)])})
    html = got[legacy(fid, "รอท่านอนุมัติ")]["html"][0]
    assert f"{FE}/finance/approvals?doc={fid}" in html and "ขั้นที่ 1 จาก 2" in html

    # pending queue
    pend = a.c.get("/finance/approvals/pending", params={"employee_id": LEAD}).json()
    assert [p["form_id"] for p in pend] == [fid]
    assert a.c.get("/finance/approvals/pending", params={"employee_id": REQ}).json() == []

    # requester cannot approve own; lv2 peer cannot
    a.approve(fid, REQ, expect=403)

    res = a.approve(fid, LEAD)
    assert (res["status"], res["current_level"]) == ("In Progress", 2)
    got = a.mails("2 approve step1 (LEAD lv3)", fid)
    assert_mails(got, {legacy(fid, "รอท่านอนุมัติ"): ([email_of(MGR)], [email_of(REQ)])})
    assert "ขั้นที่ 2 จาก 2" in got[legacy(fid, "รอท่านอนุมัติ")]["html"][0]
    a.approve(fid, LEAD, expect=403)  # step-1 approver cannot take step 2

    res = a.approve(fid, MGR)
    assert res["status"] == "Approved"
    assert a.status(fid) == "AWAITING_VOUCHER"
    got = a.mails("3 approve step2 (MGR lv5)", fid)
    assert_mails(got, {summary(fid, "อนุมัติครบทุกขั้นแล้ว"): ([email_of(REQ)], [ACCOUNT_BOX])})

    # voucher: permission enforced, no email
    a.voucher(fid, who=LEAD, expect=403)  # not finance/accounting
    a.voucher(fid, who=FP, expect=403)    # finance only records payments
    a.voucher(fid)
    assert a.status(fid) == "AWAITING_PAYMENT"
    assert a.mails("4 voucher", fid) == {}

    a.pay(fid, 12000, who=FV, expect=403)
    a.pay(fid, 12000)
    d = a.detail(fid)
    assert d["status"] == "AWAITING_CLEARING"
    due = (today() + timedelta(days=7))
    assert d["fin"]["clear_due_date"] == due.isoformat()
    got = a.mails("5 pay 12,000", fid)
    assert_mails(got, {legacy(fid, "การเงินจ่ายเงินเรียบร้อย"): ([email_of(REQ)], [])})

    # clear 9,500 → refund 2,500 needs settle_date
    a.clear(fid, [5000, 4500], settle_date=False, expect=400)
    a.clear(fid, [5000, 4500], who=LEAD, expect=403)
    assert a.mails() == {}
    d = a.clear(fid, [5000, 4500])
    assert d["status"] == "CLEARING_APPROVAL"
    assert (d["fin"]["amount_actual"], d["fin"]["settle_amount"]) == (9500.0, 2500.0)
    assert a.detail(fid)["clear_approval"] == {"step": 1, "total_steps": 2, "clause": "6.5", "required_level": 3}
    got = a.mails("6 clear 9,500 (refund 2,500)", fid)
    assert_mails(got, {
        summary(fid, "ส่งเคลียร์เงินแล้ว"): ([email_of(REQ)], [ACCOUNT_BOX]),
        summary(fid, "รอท่านอนุมัติการเคลียร์"): ([email_of(LEAD), email_of(ASM), email_of(MGR)], [email_of(REQ)]),
    })
    pending_html = got[summary(fid, "รอท่านอนุมัติการเคลียร์")]["html"][0]
    assert f"{FE}/finance/approvals?tab=clear&amp;doc={fid}" in pending_html
    assert "คืนบริษัท" in pending_html and "2,500.00 บาท" in pending_html

    q = a.c.get("/finance/clear-approvals/pending", params={"employee_id": LEAD}).json()
    assert [(x["form_id"], x["step"], x["total_steps"]) for x in q] == [(fid, 1, 2)]
    assert a.c.get("/finance/clear-approvals/pending", params={"employee_id": REQ}).json() == []

    a.put(fid, "clear-approve", {"action_by": FV}, expect=403)  # accounting is not a clearing approver
    d = a.put(fid, "clear-approve", {"action_by": LEAD})
    assert d["status"] == "CLEARING_APPROVAL" and d["fin"]["fin_status"] == "CLEAR_PENDING"
    got = a.mails("7 clear-approve step1 (LEAD)", fid)
    assert_mails(got, {summary(fid, "รอท่านอนุมัติการเคลียร์"): ([email_of(MGR)], [email_of(REQ)])})
    a.put(fid, "clear-approve", {"action_by": LEAD}, expect=403)

    d = a.put(fid, "clear-approve", {"action_by": MGR})
    assert d["status"] == "AWAITING_REVIEW"
    got = a.mails("8 clear-approve step2 (MGR)", fid)
    letter_subject = "การเคลียร์เงิน Advance ผ่านการอนุมัติครบทุกขั้นแล้ว สมชาย ใจดี"
    assert_mails(got, {letter_subject: ([email_of(REQ)], [ACCOUNT_BOX])})
    letter = got[letter_subject]["html"][0]
    for text in ("เรียน คุณสมชาย,", f"จากใบคำขอเบิกเงิน {fid} จำนวน 12,000.00 บาท",
                 f"ครบกำหนดชำระในวันที่ {due.strftime('%d/%m/%Y')}", "พิมพ์แบบฟอร์มใบสรุปการเคลียร์เงิน",
                 "สแกนเอกสารการเคลียร์ให้ทางบัญชีก่อน", "ติดต่อคุณเบล", ACCOUNT_BOX, f"{FE}/finance/advance/{fid}"):
        assert text in letter, text

    a.put(fid, "confirm", {"action_by": FP}, expect=403)  # closing is accounting's job
    d = a.put(fid, "confirm", {"action_by": FC, "clear_doc_no": "CL-1"})
    assert d["status"] == "CLOSED" and d["fin"]["clear_doc_no"] == "CL-1"
    got = a.mails("9 confirm/close", fid)
    assert_mails(got, {legacy(fid, "รายการเบิกเงินปิดเรียบร้อย"): ([email_of(REQ)], [])})

    print("\nHAPPY PATH TRACE")
    for label, status, mails in TRACE:
        print(f"  {label:32s} {status:18s}")
        for subject, to, cc in mails:
            print(f"      {subject} | to={to} | cc={cc}")
    TRACE.clear()


# ================================================================ 2. extra pay

def test_extra_pay_path_one_click_approval(api):
    a = api
    fid = a.submit(3000)  # 6.6 → steps lv3, lv4
    got = a.mails()
    assert_mails(got, {legacy(fid, "รอท่านอนุมัติ"): (
        [email_of(LEAD), email_of(ASM), email_of(MGR)], [email_of(REQ)])})
    res = a.approve(fid, ASM)  # lv4 satisfies step 2 too → finished in one click
    assert res["status"] == "Approved"
    assert_mails(a.mails(), {summary(fid, "อนุมัติครบทุกขั้นแล้ว"): ([email_of(REQ)], [ACCOUNT_BOX])})
    a.voucher(fid)
    a.pay(fid, 3000)
    a.mails()

    d = a.clear(fid, [2000, 1600], settle_date=False)  # 3,600 > 3,000 → company pays 600 more
    assert d["fin"]["settle_amount"] == -600.0 and d["fin"]["settle_date"] is None
    assert d["status"] == "CLEARING_APPROVAL"
    got = a.mails()
    assert set(got) == {summary(fid, "ส่งเคลียร์เงินแล้ว"), summary(fid, "รอท่านอนุมัติการเคลียร์")}
    assert "บริษัทจ่ายเพิ่ม" in got[summary(fid, "ส่งเคลียร์เงินแล้ว")]["html"][0]
    d = a.put(fid, "clear-approve", {"action_by": LEAD})  # 6.6 for clearing = one step (lv3)
    assert d["status"] == "AWAITING_REVIEW"
    assert set(a.mails()) == {"การเคลียร์เงิน Advance ผ่านการอนุมัติครบทุกขั้นแล้ว สมชาย ใจดี"}

    a.put(fid, "confirm", {"action_by": FC}, expect=400)  # extra pay needs the transfer date
    extra_day = (today() + timedelta(days=1)).isoformat()
    d = a.put(fid, "confirm", {"action_by": FC, "settle_date": extra_day})
    assert d["status"] == "CLOSED" and d["fin"]["settle_date"] == extra_day
    assert_mails(a.mails(), {legacy(fid, "รายการเบิกเงินปิดเรียบร้อย"): ([email_of(REQ)], [])})


# ================================================================ 3. unhappy paths

def test_reject_at_step_one(api):
    a = api
    fid = a.submit(12000)
    a.mails()
    a.reject(fid, LEAD, remark="  ", expect=400)
    a.reject(fid, EXEC, remark="ยังไม่ถึงเวลา")  # any eligible approver of the current step may reject
    assert a.status(fid) == "REJECTED"
    got = a.mails()
    assert_mails(got, {legacy(fid, "คำขอเบิกเงินไม่ได้รับการอนุมัติ"): ([email_of(REQ)], [])})
    assert "ยังไม่ถึงเวลา" in got[legacy(fid, "คำขอเบิกเงินไม่ได้รับการอนุมัติ")]["html"][0]
    a.approve(fid, MGR, expect=400)


def test_return_resubmit_reapprove_with_superadmin(api):
    a = api
    fid = a.submit(4000)
    a.mails()
    a.approve(fid, ASM)
    a.voucher(fid)
    assert a.status(fid) == "AWAITING_PAYMENT"
    a.mails()

    a.put(fid, "return", {"action_by": FV, "remark": " "}, expect=400)
    a.put(fid, "return", {"action_by": FP, "remark": "แนบใบเสนอราคา"}, expect=403)
    d = a.put(fid, "return", {"action_by": FV, "remark": "แนบใบเสนอราคา"})
    assert d["status"] == "RETURNED" and d["fin"]["voucher_no"] is None
    got = a.mails()
    assert_mails(got, {legacy(fid, "บัญชีตีกลับให้แก้ไข"): ([email_of(REQ)], [])})

    a.put(fid, "resubmit", {"action_by": LEAD}, expect=403)
    d = a.put(fid, "resubmit", {"action_by": REQ})
    assert d["status"] == "PENDING_APPROVAL"
    got = a.mails()
    assert_mails(got, {legacy(fid, "รอท่านอนุมัติ (ส่งใหม่หลังแก้ไข)"): (
        [email_of(LEAD), email_of(ASM), email_of(MGR)], [email_of(REQ)])})

    # superadmin (lv4, other department) approves step 1 — counts for that step only, no skip
    res = a.approve(fid, SUPER)
    assert (res["status"], res["current_level"]) == ("In Progress", 2)
    got = a.mails()
    # step 2 needs lv4: ASM + MGR; the superadmin is never on an approver list
    assert_mails(got, {legacy(fid, "รอท่านอนุมัติ"): ([email_of(ASM), email_of(MGR)], [email_of(REQ)])})
    res = a.approve(fid, ASM)
    assert res["status"] == "Approved"
    assert a.status(fid) == "AWAITING_VOUCHER"
    a.mails()
    a.voucher(fid)
    assert a.status(fid) == "AWAITING_PAYMENT"


def test_superadmin_can_take_step_two_and_clearing(api):
    a = api
    fid = a.submit(12000)
    a.approve(fid, LEAD)
    res = a.approve(fid, SUPER)  # superadmin finishes step 2 (needs lv5) though his own level is 4
    assert res["status"] == "Approved"
    # NOTE: the superadmin can also take BOTH request steps alone (no step-1 exclusion for him) — see report
    f2 = a.submit(12000)
    assert a.approve(f2, SUPER)["status"] == "In Progress"
    assert a.approve(f2, SUPER)["status"] == "Approved"
    a.voucher(fid)
    a.pay(fid, 12000)
    a.clear(fid, [12000])
    a.mails()
    for _ in range(2):
        a.put(fid, "clear-approve", {"action_by": SUPER})
    # NOTE: the same superadmin could approve BOTH clearing steps (see report) — documents current behaviour
    assert a.status(fid) == "AWAITING_REVIEW"


def test_clear_reject_then_reclear(api):
    a = api
    fid = a.paid_advance(4000)
    d = a.clear(fid, [1500])
    assert d["status"] == "CLEARING_APPROVAL"
    a.mails()
    a.put(fid, "clear-reject", {"action_by": LEAD, "review_remark": " "}, expect=400)
    a.put(fid, "clear-reject", {"action_by": FR, "review_remark": "ใบเสร็จไม่ชัด"}, expect=403)
    d = a.put(fid, "clear-reject", {"action_by": LEAD, "review_remark": "ใบเสร็จไม่ชัด"})
    assert d["status"] == "SENT_BACK" and d["fin"]["review_remark"] == "ใบเสร็จไม่ชัด"
    TRACE.append(("clear-reject emails", d["status"], list(a.mails())))
    rejected_mails = TRACE.pop()[2]

    d = a.clear(fid, [1500])
    assert d["status"] == "CLEARING_APPROVAL"  # never approved before → needs approval again
    got = a.mails()
    assert set(got) == {summary(fid, "ส่งเคลียร์เงินแล้ว"), summary(fid, "รอท่านอนุมัติการเคลียร์")}
    d = a.put(fid, "clear-approve", {"action_by": LEAD})  # the rejecter may approve the new round
    assert d["status"] == "AWAITING_REVIEW"
    assert rejected_mails == [legacy(fid, "หัวหน้าไม่อนุมัติการเคลียร์")]


def test_clear_reject_should_email_requester(api):
    a = api
    fid = a.paid_advance(4000)
    a.clear(fid, [1500])
    a.mails()
    a.put(fid, "clear-reject", {"action_by": LEAD, "review_remark": "ใบเสร็จไม่ชัด"})
    got = a.mails()
    assert_mails(got, {legacy(fid, "หัวหน้าไม่อนุมัติการเคลียร์"): ([email_of(REQ)], [])})
    assert "ใบเสร็จไม่ชัด" in got[legacy(fid, "หัวหน้าไม่อนุมัติการเคลียร์")]["html"][0]


def test_send_back_then_reclear_same_and_crossing_tier(api):
    a = api
    fid = a.paid_advance(4000)
    a.clear(fid, [1500])  # 6.7 → clearing 1 step (lv3)
    a.put(fid, "clear-approve", {"action_by": LEAD})
    assert a.status(fid) == "AWAITING_REVIEW"
    a.mails()

    a.put(fid, "send-back", {"action_by": FP, "review_remark": "ขาดใบเสร็จ"}, expect=403)
    d = a.put(fid, "send-back", {"action_by": FR, "review_remark": "ขาดใบเสร็จ"})
    assert d["status"] == "SENT_BACK"
    got = a.mails()
    assert_mails(got, {legacy(fid, "บัญชีตีกลับเอกสารเคลียร์"): ([email_of(REQ)], [])})
    assert "ขาดใบเสร็จ" in got[legacy(fid, "บัญชีตีกลับเอกสารเคลียร์")]["html"][0]

    # same tier (6.7) → straight back to accounting, no head approval, print steps in the mail
    d = a.clear(fid, [1800])
    assert d["status"] == "AWAITING_REVIEW"
    got = a.mails()
    assert_mails(got, {summary(fid, "ส่งเคลียร์เงินแล้ว"): ([email_of(REQ)], [ACCOUNT_BOX])})
    assert "ขั้นต่อไป ส่งเอกสารตัวจริงให้บัญชี" in got[summary(fid, "ส่งเคลียร์เงินแล้ว")]["html"][0]

    # send back again, re-clear crossing into 6.6 → head approval again
    a.put(fid, "send-back", {"action_by": FR, "review_remark": "ยอดไม่ตรง"})
    a.mails()
    d = a.clear(fid, [3900], settle_date=True)
    assert d["status"] == "CLEARING_APPROVAL"
    assert set(a.mails()) == {summary(fid, "ส่งเคลียร์เงินแล้ว"), summary(fid, "รอท่านอนุมัติการเคลียร์")}


def test_clearing_mgr_clicks_step_one_first(api):
    """Same as the request flow: a lv5 manager approving clearing step 1 also satisfies step 2 (lv5) — one click."""
    a = api
    fid = a.paid_advance(12000, approver=LEAD)
    a.clear(fid, [10000])  # 6.5 → clearing steps lv3, lv5; step-1 mail goes to LEAD/ASM/MGR
    a.mails()
    d = a.put(fid, "clear-approve", {"action_by": MGR})
    assert d["status"] == "AWAITING_REVIEW"
    got = a.mails()
    assert len(got) == 1 and next(iter(got)).startswith("การเคลียร์เงิน Advance ผ่านการอนุมัติครบทุกขั้นแล้ว")
    approved = [r for r in a.detail(fid)["clear_logs"] if r["action"] == "APPROVED"]
    assert [r["step"] for r in approved] == [1, 2] and {r["action_by"] for r in approved} == {MGR}


def test_clear_with_zero_spent_is_handled(api):
    a = api
    fid = a.paid_advance(4000)
    r = a.c.put(f"/finance/advances/{fid}/clear", json={
        "action_by": REQ, "clear_date": today().isoformat(), "settle_date": today().isoformat(),
        "items": [{"expense_date": today().isoformat(), "description": "ไม่ได้ใช้", "amount_before_vat": "0"}]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "AWAITING_REVIEW"  # nothing spent → no head approval, straight to accounting


# ================================================================ 4. test-box redirect + account box override

def test_email_test_to_redirects_with_banner(api, monkeypatch):
    monkeypatch.setenv("FINANCE_EMAIL_TEST_TO", "uat@test.local")
    a = api
    fid = a.submit(12000)
    sent = a.outbox.take()
    assert len(sent) == 1
    m = sent[0]
    assert (m["to"], m["cc"], m["envelope"]) == ("uat@test.local", [], ["uat@test.local"])
    assert m["subject"] == legacy(fid, "รอท่านอนุมัติ")
    assert "อีเมลทดสอบ · ระบบจริงจะส่งถึง:" in m["html"]
    for real in (email_of(LEAD), email_of(ASM), email_of(MGR), email_of(REQ)):
        assert real in m["html"], real


def test_account_mailbox_override(api, monkeypatch):
    monkeypatch.setenv("FINANCE_ACCOUNT_EMAIL", "acc-uat@test.local")
    a = api
    fid = a.submit(3000)
    a.mails()
    a.approve(fid, ASM)
    assert_mails(a.mails(), {summary(fid, "อนุมัติครบทุกขั้นแล้ว"): ([email_of(REQ)], ["acc-uat@test.local"])})


# ================================================================ model drift

def test_model_check_constraint_matches_prod_sql():
    ck = next(c for c in FinAdvance.__table__.constraints if getattr(c, "name", None) == "ck_fin_advances_fin_status")
    assert "CLEAR_PENDING" in str(ck.sqltext)
