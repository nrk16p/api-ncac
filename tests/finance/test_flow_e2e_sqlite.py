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
    "form_approver_divisions",
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
    "F010": (13, "ผู้ช่วย", "หัวหน้าบัญชี", 2, ACC_DEPT, "acc2@test.local"),     # ผู้เบิกแผนกบัญชี
    "690041": (14, "สุขพัฒน์", "ขั้นพิเศษ", 4, ACC_DEPT, "suk@test.local"),      # ขั้นพิเศษแผนกบัญชี (26/10/2026)
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
    db.add(FinPayeeAccount(employee_id="F010", bank="KBANK", account_no="1234567891", account_name="ผู้ช่วย หัวหน้าบัญชี",
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
    # ไล่ขั้นตาม TOA สูงสุด 2 ขั้น (ผู้ใช้สั่ง 26/10/2026): 6.5 ผู้เบิก lv2 → ขั้น 1 lv3 แล้วขั้น 2 lv5
    # อีเมลขั้น 1 ถึงหัวหน้า lv3 คนเดียว ไม่ถึง lv4/5/8/9/superadmin
    assert_mails(got, {legacy(fid, "รอท่านอนุมัติ"): ([email_of(LEAD)], [email_of(REQ)])})
    html = got[legacy(fid, "รอท่านอนุมัติ")]["html"][0]
    assert f"{FE}/finance/approvals?doc={fid}" in html and "ขั้นที่ 1 จาก 2" in html

    pend = a.c.get("/finance/approvals/pending", params={"employee_id": LEAD}).json()
    assert [p["form_id"] for p in pend] == [fid]
    assert a.c.get("/finance/approvals/pending", params={"employee_id": REQ}).json() == []

    a.approve(fid, REQ, expect=403)  # requester cannot approve own

    a.approve(fid, LEAD)
    assert a.status(fid) == "PENDING_APPROVAL"
    got = a.mails("2 approve step 1 (LEAD lv3)", fid)
    assert_mails(got, {legacy(fid, "รอท่านอนุมัติ"): ([email_of(MGR)], [email_of(REQ)])})
    assert "ขั้นที่ 2 จาก 2" in got[legacy(fid, "รอท่านอนุมัติ")]["html"][0]

    a.approve(fid, LEAD, expect=403)  # the step-1 approver cannot take step 2
    a.approve(fid, ASM, expect=403)   # lv4 is below step 2 (lv5)
    res = a.approve(fid, MGR)
    assert res["status"] == "Approved"
    assert a.status(fid) == "AWAITING_VOUCHER"
    got = a.mails("3 approve step 2 (MGR lv5)", fid)
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
        summary(fid, "รอท่านอนุมัติการเคลียร์"): ([email_of(LEAD)], [email_of(REQ)]),
    })
    pending_html = got[summary(fid, "รอท่านอนุมัติการเคลียร์")]["html"][0]
    assert f"{FE}/finance/approvals?tab=clear&amp;doc={fid}" in pending_html
    assert "คืนบริษัท" in pending_html and "2,500.00 บาท" in pending_html

    q = a.c.get("/finance/clear-approvals/pending", params={"employee_id": LEAD}).json()
    assert [(x["form_id"], x["step"], x["total_steps"]) for x in q] == [(fid, 1, 2)]
    assert a.c.get("/finance/clear-approvals/pending", params={"employee_id": REQ}).json() == []

    a.put(fid, "clear-approve", {"action_by": FV}, expect=403)    # accounting is not a clearing approver
    d = a.put(fid, "clear-approve", {"action_by": LEAD})
    assert d["status"] == "CLEARING_APPROVAL"
    assert a.detail(fid)["clear_approval"]["step"] == 2
    got = a.mails("7a clear-approve step 1 (LEAD)", fid)
    assert_mails(got, {summary(fid, "รอท่านอนุมัติการเคลียร์"): ([email_of(MGR)], [email_of(REQ)])})
    a.put(fid, "clear-approve", {"action_by": LEAD}, expect=403)  # the step-1 approver cannot take step 2
    d = a.put(fid, "clear-approve", {"action_by": MGR})
    assert d["status"] == "AWAITING_REVIEW"
    got = a.mails("7b clear-approve step 2 (MGR)", fid)
    letter_subject = "การเคลียร์เงิน Advance ผ่านการอนุมัติครบทุกขั้นแล้ว สมชาย ใจดี"
    assert_mails(got, {letter_subject: ([email_of(REQ)], [ACCOUNT_BOX])})
    letter = got[letter_subject]["html"][0]
    for text in ("เรียน คุณสมชาย,", f"จากใบคำขอเบิกเงิน {fid} จำนวน 12,000.00 บาท",
                 f"ครบกำหนดชำระในวันที่ {due.strftime('%d/%m/%Y')}", "พิมพ์แบบฟอร์มใบสรุปการเคลียร์เงิน",
                 "สแกนเอกสารการเคลียร์ให้ทางบัญชีก่อน", "ติดต่อคุณเบล", ACCOUNT_BOX, f"{FE}/finance/advance/{fid}"):
        assert text in letter, text

    a.put(fid, "confirm", {"action_by": FP}, expect=403)  # closing is accounting's job
    got_today = today().isoformat()
    a.put(fid, "confirm", {"action_by": FC, "clear_doc_no": "CL691000000001"}, expect=400)  # ยังไม่ติ๊กรับเอกสารตัวจริง
    a.put(fid, "confirm", {"action_by": FC, "clear_doc_no": "CL-1", "original_received_date": got_today}, expect=400)  # มีขีด ไม่ใช่ตัวเลข/อักษรอังกฤษล้วน
    d = a.put(fid, "confirm", {"action_by": FC, "clear_doc_no": "CL691000000001", "original_received_date": got_today})
    assert d["status"] == "CLOSED" and d["fin"]["clear_doc_no"] == "CL691000000001"
    assert d["fin"]["original_received_date"] == got_today
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
    fid = a.submit(3000)  # 6.6 → one step lv3
    got = a.mails()
    assert_mails(got, {legacy(fid, "รอท่านอนุมัติ"): ([email_of(LEAD)], [email_of(REQ)])})
    res = a.approve(fid, ASM)  # a higher level (lv4, not emailed) may approve through the link
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
    d = a.put(fid, "confirm", {"action_by": FC, "settle_date": extra_day, "clear_doc_no": "CL691000000002",
                               "original_received_date": today().isoformat()})
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


def test_finance_sends_a_wrong_voucher_back_to_accounting(api):
    """ผู้ใช้แจ้ง 26/10/2026: บัญชีตั้งเบิกแล้วใส่เลขผิด การเงินกดย้อนกลับ บัญชีแก้เลขแล้วการเงินจ่ายต่อได้
    (ไม่ต้องกลับไปอนุมัติใหม่ ต่างจากตีกลับให้ผู้เบิกแก้ไข)."""
    a = api
    fid = a.submit(3000)
    assert a.approve(fid, ASM)["status"] == "Approved"
    a.voucher(fid)
    a.outbox.take()
    a.put(fid, "reject-voucher", {"action_by": FP, "remark": "  "}, expect=422)  # schema: reason required
    a.put(fid, "reject-voucher", {"action_by": REQ, "remark": "เลขผิด"}, expect=403)
    d = a.put(fid, "reject-voucher", {"action_by": FP, "remark": "เลขตั้งเบิกผิด"})
    assert d["status"] == "AWAITING_VOUCHER" and d["fin"]["fin_status"] == "VOUCHER_REJECTED"
    assert d["approval_logs"][-1]["action"] == "APPROVED"  # approval stands
    d = a.put(fid, "voucher", {"action_by": FV, "voucher_no": "PV-2", "voucher_date": today().isoformat()})
    assert d["status"] == "AWAITING_PAYMENT" and d["fin"]["voucher_no"] == "PV-2"
    assert [log["action"] for log in d["fin_logs"]] == ["VOUCHER", "VOUCHER_REJECT", "VOUCHER"]
    assert a.pay(fid, 3000)["status"] == "AWAITING_CLEARING"
    a.put(fid, "reject-voucher", {"action_by": FP, "remark": "ช้าไป"}, expect=409)  # paid already


def test_finance_edits_wrong_doc_numbers_even_after_close(api):
    """ผู้ใช้สั่ง 26/10/2026: บัญชีใส่เลขผิด การเงินแก้เลขที่ตั้งเบิก เลขที่ทำจ่าย และเลขที่เคลียร์ได้เอง (ต้องใส่เหตุผล)"""
    a = api
    fid = a.paid_advance(3000)  # PV-1 / PAY-1

    def edit(body, who=FP, expect=200):
        return a.put(fid, "doc-numbers", {"action_by": who, "remark": "พิมพ์เลขผิด", **body}, expect)

    edit({"voucher_no": "PV-9"}, who=FV, expect=403)  # การเงิน (สิทธิ์จ่ายเงิน) เท่านั้น
    edit({"voucher_no": "PV-9", "remark": "  "}, expect=400)
    edit({"clear_doc_no": "CL1"}, expect=409)  # ยังไม่มีเลขที่เคลียร์
    edit({"voucher_no": "PV-1"}, expect=400)  # ไม่มีอะไรเปลี่ยน
    d = edit({"voucher_no": "PV-9", "payment_doc_no": "PAY-9"})
    assert d["status"] == "AWAITING_CLEARING"
    assert (d["fin"]["voucher_no"], d["fin"]["payment_doc_no"]) == ("PV-9", "PAY-9")
    log = d["fin_logs"][-1]
    assert (log["action"], log["remark"], log["action_by"]) == ("DOC_NO_EDIT", "พิมพ์เลขผิด", FP)
    assert log["changes"] == {"voucher_no": ["PV-1", "PV-9"], "payment_doc_no": ["PAY-1", "PAY-9"]}

    a.clear(fid, [3000], settle_date=False)
    a.put(fid, "clear-approve", {"action_by": LEAD})
    d = a.put(fid, "confirm", {"action_by": FC, "clear_doc_no": "CL691000000001", "original_received_date": today().isoformat()})
    assert d["status"] == "CLOSED"
    edit({"clear_doc_no": "CL-2"}, expect=400)  # ตัวเลข/อักษรอังกฤษเท่านั้น เหมือนตอนปิดรายการ
    edit({"clear_doc_no": "CL69100099"}, expect=400)  # ต้อง 14 หลัก
    a.mails()  # อีเมลเคลียร์/ปิดรายการก่อนหน้า
    d = edit({"clear_doc_no": "CL691000000099"}, who=SUPER)
    assert d["status"] == "CLOSED" and d["fin"]["clear_doc_no"] == "CL691000000099"
    assert d["fin_logs"][-1]["changes"] == {"clear_doc_no": ["CL691000000001", "CL691000000099"]}
    assert a.mails() == {}  # แก้เลขไม่ส่งอีเมล


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
    # 4,000 → 6.6 lv3: only the lead is emailed; the superadmin is never on an approver list
    assert_mails(got, {legacy(fid, "รอท่านอนุมัติ (ส่งใหม่หลังแก้ไข)"): ([email_of(LEAD)], [email_of(REQ)])})

    # superadmin (lv4, other department) may approve the single step
    res = a.approve(fid, SUPER)
    assert res["status"] == "Approved"
    assert a.status(fid) == "AWAITING_VOUCHER"
    a.mails()
    a.voucher(fid)
    assert a.status(fid) == "AWAITING_PAYMENT"


def test_superadmin_can_take_each_step_and_clearing(api):
    a = api
    fid = a.submit(12000)             # 6.5 → ขั้น lv3 แล้ว lv5
    res = a.approve(fid, SUPER)       # superadmin กดแทนขั้น 1 ได้ แต่ไม่ข้ามขั้น 2 ด้วยระดับตัวเอง (lv4)
    assert res["status"] == "In Progress"
    res = a.approve(fid, SUPER)       # แล้วกดขั้น 2 ต่อได้
    assert res["status"] == "Approved"
    a.voucher(fid)
    a.pay(fid, 12000)
    a.clear(fid, [12000])
    a.mails()
    a.put(fid, "clear-approve", {"action_by": SUPER})
    assert a.status(fid) == "CLEARING_APPROVAL"
    a.put(fid, "clear-approve", {"action_by": SUPER})
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


def test_clear_queue_splits_mine_and_delegable_like_the_request_queue(api):
    """แท็บรออนุมัติเคลียร์แยก รอฉันอนุมัติ / อนุมัติแทนได้ แบบเดียวกับคิวขอเบิก (ผู้ใช้สั่ง 26/10/2026)
    ระดับต่ำสุดที่กดขั้นนี้ได้ = mine · ระดับสูงกว่า = delegable · superadmin = mine"""
    a = api
    fid = a.paid_advance(12000, approver=MGR)        # 6.5 → เคลียร์ขั้น lv3 แล้ว lv5
    a.clear(fid, [9500])

    def queue(who):
        rows = a.c.get("/finance/clear-approvals/pending", params={"employee_id": who}).json()
        return [(r["form_id"], r["tab"], r["step_required_level"]) for r in rows]

    assert queue(LEAD) == [(fid, "mine", 3)]
    assert queue(ASM) == [(fid, "delegable", 3)]
    assert queue(MGR) == [(fid, "delegable", 3)]
    assert queue(SUPER) == [(fid, "mine", 3)]

    a.put(fid, "clear-approve", {"action_by": LEAD})
    assert queue(MGR) == [(fid, "mine", 5)]
    assert queue(EXEC) == [(fid, "delegable", 5)]
    assert queue(LEAD) == []                         # คนกดขั้น 1 กดขั้น 2 ไม่ได้


def test_clear_queue_special_step_is_mine_for_690041(api):
    a = api
    fid = a.submit(5000, who="F010")
    a.approve(fid, "F003")
    a.approve(fid, "690041")
    a.voucher(fid)
    a.pay(fid, 5000)
    a.clear(fid, [4000], who="F010")
    a.put(fid, "clear-approve", {"action_by": "F003"})
    rows = a.c.get("/finance/clear-approvals/pending", params={"employee_id": "690041"}).json()
    assert [(r["form_id"], r["tab"]) for r in rows] == [(fid, "mine")]


def test_clear_waiting_past_the_last_step_counts_as_the_last_step(api):
    """ใบที่ค้างรออนุมัติเคลียร์ตอนเปลี่ยนกติกา (26/10/2026) อาจรออยู่ขั้นที่สายใหม่ไม่มี
    คิวรออนุมัติต้องไม่พังทั้งหน้า และกดอนุมัติขั้นสุดท้ายของสายใหม่ได้"""
    a = api
    fid = a.paid_advance(4000)                 # ใบขอ 6.6 → เคลียร์ขั้นเดียว lv3
    a.clear(fid, [1500])
    a.mails()
    db = a.Session()
    try:
        adv = db.query(FinAdvance).filter(FinAdvance.form_id == fid).one()
        adv.clear_approval_level = 2           # ค้างจากสายเดิมที่มีสองขั้น
        db.commit()
    finally:
        db.close()

    r = a.c.get("/finance/clear-approvals/pending", params={"employee_id": LEAD})
    assert r.status_code == 200, r.text
    assert [(x["form_id"], x["step"], x["total_steps"]) for x in r.json()] == [(fid, 1, 1)]
    assert a.detail(fid)["clear_approval"]["step"] == 1
    assert a.put(fid, "clear-approve", {"action_by": LEAD})["status"] == "AWAITING_REVIEW"


def test_send_back_then_reclear_skips_a_second_approval(api):
    """ขั้นอนุมัติเคลียร์ตามใบขอ (26/10/2026) จึงไม่มีทาง "ข้ามชั้น" ตอนแก้ส่งใหม่แล้ว
    อนุมัติเคลียร์ครบไปแล้ว บัญชีส่งกลับ ผู้เบิกแก้ยอดเท่าไหร่ก็กลับไปรอบัญชีตรวจเลย"""
    a = api
    fid = a.paid_advance(4000)
    a.clear(fid, [1500])  # ใบขอ 4,000 (6.6) → เคลียร์ขั้นเดียว lv3
    a.put(fid, "clear-approve", {"action_by": LEAD})
    assert a.status(fid) == "AWAITING_REVIEW"
    a.mails()

    a.put(fid, "send-back", {"action_by": FP, "review_remark": "ขาดใบเสร็จ"}, expect=403)
    d = a.put(fid, "send-back", {"action_by": FR, "review_remark": "ขาดใบเสร็จ"})
    assert d["status"] == "SENT_BACK"
    got = a.mails()
    assert_mails(got, {legacy(fid, "บัญชีตีกลับเอกสารเคลียร์"): ([email_of(REQ)], [])})
    assert "ขาดใบเสร็จ" in got[legacy(fid, "บัญชีตีกลับเอกสารเคลียร์")]["html"][0]

    # straight back to accounting, no head approval, print steps in the mail
    d = a.clear(fid, [1800])
    assert d["status"] == "AWAITING_REVIEW"
    got = a.mails()
    assert_mails(got, {summary(fid, "ส่งเคลียร์เงินแล้ว"): ([email_of(REQ)], [ACCOUNT_BOX])})
    assert "ขั้นต่อไป ส่งเอกสารตัวจริงให้บัญชี" in got[summary(fid, "ส่งเคลียร์เงินแล้ว")]["html"][0]

    # send back again, re-clear far above the old amount → still straight back to accounting
    a.put(fid, "send-back", {"action_by": FR, "review_remark": "ยอดไม่ตรง"})
    a.mails()
    d = a.clear(fid, [6000], settle_date=False)
    assert d["status"] == "AWAITING_REVIEW"
    assert set(a.mails()) == {summary(fid, "ส่งเคลียร์เงินแล้ว")}


def test_clearing_follows_the_toa_ladder_and_a_manager_may_skip(api):
    """เคลียร์ 10,000 (6.5) → ขั้น lv3 แล้ว lv5: อีเมลขั้น 1 ถึงหัวหน้า · ผู้จัดการกดขั้น 1 แล้วข้ามขั้น 2 จบในครั้งเดียว"""
    a = api
    fid = a.paid_advance(12000, approver=MGR)
    a.clear(fid, [10000])
    got = a.mails()
    assert got[summary(fid, "รอท่านอนุมัติการเคลียร์")]["to"] == [email_of(LEAD)]
    d = a.put(fid, "clear-approve", {"action_by": MGR})
    assert d["status"] == "AWAITING_REVIEW"
    got = a.mails()
    assert len(got) == 1 and next(iter(got)).startswith("การเคลียร์เงิน Advance ผ่านการอนุมัติครบทุกขั้นแล้ว")
    approved = [r for r in a.detail(fid)["clear_logs"] if r["action"] == "APPROVED"]
    assert [r["step"] for r in approved] == [1, 2] and {r["action_by"] for r in approved} == {MGR}


def test_clear_with_zero_spent_still_goes_through_the_request_steps(api):
    """ADV-2610-018 (ผู้ใช้สั่ง 26/10/2026 "no matter what - still need approve same like when เบิก"):
    คืนทั้งก้อน ใช้จริง 0 ก็ต้องอนุมัติเคลียร์ครบขั้นเหมือนตอนขอเบิก — เดิมส่งตรงไปรอบัญชีตรวจ
    ผู้เบิกแผนกบัญชีขอ 5,000 → หัวหน้าบัญชี แล้ว 690041 ทั้งตอนขอและตอนเคลียร์"""
    a = api
    fid = a.submit(5000, who="F010")
    a.approve(fid, "F003")
    assert a.approve(fid, "690041")["status"] == "Approved"
    a.voucher(fid)
    a.pay(fid, 5000)
    a.mails()

    d = a.clear(fid, [0], who="F010")
    assert d["status"] == "CLEARING_APPROVAL"
    assert a.detail(fid)["clear_approval"] == {"step": 1, "total_steps": 2, "clause": "6.6", "required_level": 3}
    got = a.mails()
    assert set(got[summary(fid, "รอท่านอนุมัติการเคลียร์")]["to"]) == {email_of(x) for x in ("F001", "F003", "F004", "F005")}

    a.put(fid, "clear-approve", {"action_by": "F003"})
    assert a.status(fid) == "CLEARING_APPROVAL"
    assert_mails(a.mails(), {summary(fid, "รอท่านอนุมัติการเคลียร์"): ([email_of("690041")], [email_of("F010")])})
    a.put(fid, "clear-approve", {"action_by": "F001"}, expect=403)   # ขั้น 2 เป็นของ 690041 เท่านั้น
    assert a.put(fid, "clear-approve", {"action_by": "690041"})["status"] == "AWAITING_REVIEW"


def test_clear_spending_less_keeps_the_request_steps(api):
    """ขอ 12,000 (6.5 → lv3 แล้ว lv5) ใช้จริง 1,000 ก็ยังสองขั้นเหมือนตอนขอ ไม่ลดเหลือขั้นเดียว"""
    a = api
    fid = a.paid_advance(12000, approver=MGR)
    d = a.clear(fid, [1000])
    assert d["status"] == "CLEARING_APPROVAL"
    assert a.detail(fid)["clear_approval"] == {"step": 1, "total_steps": 2, "clause": "6.5", "required_level": 3}
    q = a.c.get("/finance/clear-approvals/pending", params={"employee_id": LEAD}).json()
    assert [(x["form_id"], x["step"], x["total_steps"], x["clause"]) for x in q] == [(fid, 1, 2, "6.5")]
    a.put(fid, "clear-approve", {"action_by": LEAD})
    assert a.status(fid) == "CLEARING_APPROVAL"
    a.put(fid, "clear-approve", {"action_by": ASM}, expect=403)   # ขั้น 2 ต้อง lv5 ตามใบขอ
    assert a.put(fid, "clear-approve", {"action_by": MGR})["status"] == "AWAITING_REVIEW"


def test_clear_spending_more_still_follows_the_request(api):
    """ขอ 4,000 (6.6 ขั้นเดียว lv3) ใช้จริง 6,000 ขั้นอนุมัติเคลียร์ยังตามใบขอ ไม่ขยับขึ้น"""
    a = api
    fid = a.paid_advance(4000)
    d = a.clear(fid, [6000], settle_date=False)
    assert d["status"] == "CLEARING_APPROVAL"
    assert a.detail(fid)["clear_approval"] == {"step": 1, "total_steps": 1, "clause": "6.6", "required_level": 3}
    assert a.put(fid, "clear-approve", {"action_by": LEAD})["status"] == "AWAITING_REVIEW"


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
    for real in (email_of(LEAD), email_of(REQ)):          # ขั้น 1 ของ 12,000 = หัวหน้า lv3
        assert real in m["html"], real
    for not_mailed in (email_of(MGR), email_of(ASM)):
        assert not_mailed not in m["html"], not_mailed


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


def test_load_people_carries_the_branch(api):
    """อีเมลสาขาก่อน (26/10/2026) ต้องรู้สาขาของทุกคน — load_people อ่าน users.site_id"""
    from services.finance import approval_repo
    db = api.Session()
    try:
        assert approval_repo.load_people(db)[REQ]["site_id"] == 1
    finally:
        db.close()


def test_three_toa_steps_through_the_routes(api):
    """ไล่ทุกขั้นตามชั้น TOA (ผู้ใช้สั่ง 26/10/2026): ผู้เบิก lv2 ขอ 60,000 (6.4) → lv3 → lv5 → lv8
    อีเมลแต่ละขั้นถึงคนของขั้นนั้นตอนถึงคิว"""
    a = api
    fid = a.submit(60000)
    got = a.mails("3-step submit", fid)
    assert_mails(got, {legacy(fid, "รอท่านอนุมัติ"): ([email_of(LEAD)], [email_of(REQ)])})
    assert "ขั้นที่ 1 จาก 3" in got[legacy(fid, "รอท่านอนุมัติ")]["html"][0]

    a.approve(fid, LEAD)
    got = a.mails("3-step step 1", fid)
    assert_mails(got, {legacy(fid, "รอท่านอนุมัติ"): ([email_of(MGR)], [email_of(REQ)])})
    assert "ขั้นที่ 2 จาก 3" in got[legacy(fid, "รอท่านอนุมัติ")]["html"][0]

    a.approve(fid, ASM, expect=403)   # lv4 ไม่ถึงขั้น 2 (lv5)
    a.approve(fid, MGR)
    got = a.mails("3-step step 2", fid)
    assert_mails(got, {legacy(fid, "รอท่านอนุมัติ"): ([email_of(EXEC)], [email_of(REQ)])})
    assert "ขั้นที่ 3 จาก 3" in got[legacy(fid, "รอท่านอนุมัติ")]["html"][0]

    a.approve(fid, MGR, expect=403)   # คนกดขั้นก่อนกดขั้นนี้ไม่ได้ และระดับไม่ถึง
    res = a.approve(fid, EXEC)
    assert res["status"] == "Approved"
    assert a.status(fid) == "AWAITING_VOUCHER"
    steps = [s["step"] for s in a.detail(fid)["approval"]["step_approvals"]]
    assert steps == [1, 2, 3]


def test_accounting_6_6_needs_690041_as_its_own_step(api):
    """ขั้นพิเศษแผนกบัญชี (ผู้ใช้สั่ง 26/10/2026): ยอด 1,500 หัวหน้าบัญชีกดจบ · ยอด 4,000 หัวหน้าก่อน แล้วต้อง 690041"""
    a = api
    small = a.submit(1500, who="F010")
    got = a.mails("acc 1,500", small)
    assert set(got[legacy(small, "รอท่านอนุมัติ")]["to"]) == {email_of(x) for x in ("F001", "F003", "F004", "F005")}
    assert a.approve(small, "F003")["status"] == "Approved"
    a.mails()

    fid = a.submit(4000, who="F010")
    got = a.mails("acc 4,000", fid)
    assert "ขั้นที่ 1 จาก 2" in got[legacy(fid, "รอท่านอนุมัติ")]["html"][0]
    a.approve(fid, "F003")
    got = a.mails("acc 4,000 step 1", fid)
    assert_mails(got, {legacy(fid, "รอท่านอนุมัติ"): ([email_of("690041")], [email_of("F010")])})
    a.approve(fid, "F001", expect=403)        # หัวหน้าบัญชีคนอื่นกดขั้นพิเศษไม่ได้
    res = a.approve(fid, "690041")
    assert res["status"] == "Approved"
    assert [s["step"] for s in a.detail(fid)["approval"]["step_approvals"]] == [1, 2]


def test_a_later_step_approver_may_stand_in_for_690041(api):
    """ADV-2610-022 (ผู้ใช้สั่ง 26/10/2026): คนที่อยู่ขั้นหลังในสายเดียวกัน อนุมัติแทนขั้นพิเศษ + ข้ามได้
    F010 ขอ 15,000 → ขั้น 1 หัวหน้าบัญชี · ขั้น 2 690041 · ขั้น 3 ระดับ 5 (ในชุดทดสอบมีแค่ DCEO ระดับ 9)"""
    a = api
    fid = a.submit(15000, who="F010")
    a.approve(fid, "F003")
    a.mails()
    rows = a.c.get("/finance/approvals/pending", params={"employee_id": DCEO}).json()
    assert [(r["form_id"], r["step"], r["tab"]) for r in rows] == [(fid, 2, "delegable")]
    a.approve(fid, "F001", expect=403)                 # ระดับ 3 ไม่อยู่ขั้นหลัง
    res = a.approve(fid, DCEO)
    assert res["status"] == "Approved"
    logs = [l for l in a.detail(fid)["approval_logs"] if l.get("action") == "APPROVED"]
    assert [l["level_no"] for l in logs] == [1, 2, 3]
    assert (logs[2]["remark"] or "").startswith("ผ่านขั้น 3 พร้อมการอนุมัติขั้น 2")

    # กดตั้งแต่ขั้น 1: ผ่านขั้นพิเศษแบบอนุมัติแทน และขั้น 3 ด้วยระดับ ในคลิกเดียว
    fid2 = a.submit(15000, who="F010")
    assert a.approve(fid2, DCEO)["status"] == "Approved"
    logs = [l for l in a.detail(fid2)["approval_logs"] if l.get("action") == "APPROVED"]
    assert [l["level_no"] for l in logs] == [1, 2, 3]
    assert "อนุมัติแทน" in (logs[1]["remark"] or "")
