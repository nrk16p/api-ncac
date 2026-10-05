import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

from datetime import date, timedelta
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from routes.forms import form_submission_routes as sr
from schemas.finance_schema import (PayeeAccountCreate, PayeeAccountUpdate, PayeeActionIn, PayeeRejectIn,
                                    PayeeRequestCreate)
from services.finance.payee_email import format_kbank_account, render_payee_request_email, review_link

CTX = {"request_id": 7, "employee_id": "E<1>", "employee_name": "สมชาย <b>x</b>", "department": "บัญชี",
       "position": "เจ้าหน้าที่", "account_no": "1234567890", "account_name": 'A "B" & C',
       "remark": "<script>alert(1)</script>", "app_origin": "https://app.example.com"}


def test_format_account():
    assert format_kbank_account("1234567890") == "123-4-56789-0"
    assert format_kbank_account("12") == "12"


def test_email_subject_and_escaping():
    subject, html = render_payee_request_email(CTX)
    assert subject == "[ขอเพิ่มบัญชีรับเงิน] สมชาย <b>x</b> (E<1>)"  # subject is a header, not HTML
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html
    assert "A &quot;B&quot; &amp; C" in html or "A &#x27;" in html or "&amp; C" in html
    assert "123-4-56789-0" in html and "ธนาคารกสิกรไทย" in html and "ไฟล์ bookbank ดูได้ในระบบ" in html


def test_email_link_present_and_absent():
    _, html = render_payee_request_email(CTX)
    assert 'href="https://app.example.com/finance/payee-accounts?request=7"' in html
    for bad in (None, "", "javascript:alert(1)", "https://evil.com/x", 'https://a.com"><x', "https://a.com/"):
        _, html = render_payee_request_email({**CTX, "app_origin": bad})
        assert "href=" not in html
    assert review_link("http://localhost:4000", 3) == "http://localhost:4000/finance/payee-accounts?request=3"


def test_email_missing_values_dash():
    _, html = render_payee_request_email({**CTX, "remark": None, "position": ""})
    assert "<td>-</td>" in html


# ---- schemas ----
def test_schema_limits():
    ok = dict(employee_id="E1", account_no="1" * 20, account_name="n" * 150, action_by="F1")
    PayeeAccountCreate(**ok)
    with pytest.raises(ValidationError):
        PayeeAccountCreate(**{**ok, "account_no": "1" * 21})
    with pytest.raises(ValidationError):
        PayeeAccountCreate(**{**ok, "account_name": "n" * 151})
    PayeeRequestCreate(employee_id="E1", account_no="1", account_name="n", remark="r" * 500)
    with pytest.raises(ValidationError):
        PayeeRequestCreate(employee_id="E1", account_no="1", account_name="n", remark="r" * 501)
    with pytest.raises(ValidationError):
        PayeeAccountUpdate()  # action_by required
    with pytest.raises(ValidationError):
        PayeeActionIn()


def test_reject_remark_blank_becomes_none():
    assert PayeeRejectIn(action_by="F1", review_remark="  ").review_remark is None
    with pytest.raises(ValidationError):
        PayeeRejectIn(review_remark="x")


# ---- SELF/SUPPLIER guard ----
class _Q:
    def __init__(self, master):
        self.master = master

    def filter(self, *a):
        return self

    def first(self):
        return self.master


class _Db:
    def __init__(self, master):
        self.master = master

    def query(self, *a):
        return _Q(self.master)


def _form():
    names = ["adv_amount", "adv_payee_type", "adv_bank", "adv_account_no", "adv_account_name", "adv_use_date"]
    types = ["number", "select", "select", "text", "text", "date"]
    return NS(questions=[NS(id=i + 1, question_name=n, question_type=t, sort_order=i + 1)
                         for i, (n, t) in enumerate(zip(names, types))])


def _v(qid, text=None, number=None, d=None):
    return NS(question_id=qid, value_text=text, value_number=number, value_date=d)


def _vals(payee, bank="SCB", acc="9999999999", name="hacker", drop=()):
    vals = [_v(1, number=100), _v(2, text=payee), _v(3, text=bank), _v(4, text=acc), _v(5, text=name),
            _v(6, d=date.today() + timedelta(days=5))]
    return [v for v in vals if v.question_id not in drop]


@pytest.fixture(autouse=True)
def _no_describe(monkeypatch):
    monkeypatch.setattr(sr.approval_repo, "describe", lambda *a: None)


MASTER = NS(account_no="1112223334", account_name="สมชาย", status="ACTIVE")


def test_self_overwrites_tampered_values():
    vals = _vals("SELF")
    sr._guard_advance_values(_Db(MASTER), _form(), vals, "E1")
    got = {v.question_id: v.value_text for v in vals}
    assert (got[3], got[4], got[5]) == ("KBANK", "1112223334", "สมชาย")


def test_self_without_active_master():
    for master in (None, NS(account_no="1", account_name="n", status="INACTIVE")):
        with pytest.raises(HTTPException) as ei:
            sr._guard_advance_values(_Db(master), _form(), _vals("SELF"), "E1")
        assert ei.value.status_code == 400
        assert ei.value.detail == "ยังไม่มีบัญชีรับเงินที่บัญชีอนุมัติ — กรุณาขอเพิ่มบัญชีรับเงิน"


@pytest.mark.parametrize("missing", [3, 4, 5])
def test_self_missing_value_row(missing):
    with pytest.raises(HTTPException) as ei:
        sr._guard_advance_values(_Db(MASTER), _form(), _vals("SELF", drop=(missing,)), "E1")
    assert ei.value.status_code == 400 and ei.value.detail == sr._SELF_PAYEE_INCOMPLETE


def test_payee_type_required_and_valid():
    for bad in (None, "OTHER"):
        with pytest.raises(HTTPException) as ei:
            sr._guard_advance_values(_Db(MASTER), _form(), _vals(bad), "E1")
        assert ei.value.detail == "กรุณาเลือกบัญชีรับเงิน"


def test_supplier_keeps_existing_checks():
    vals = _vals("SUPPLIER", bank="SCB", acc="123-456-7890")
    sr._guard_advance_values(_Db(None), _form(), vals, "E1")
    assert next(v for v in vals if v.question_id == 4).value_text == "1234567890"
    with pytest.raises(HTTPException) as ei:
        sr._guard_advance_values(_Db(None), _form(), _vals("SUPPLIER", bank="NOPE"), "E1")
    assert "กรุณาเลือกธนาคารจากรายการ" in ei.value.detail


def test_routes_registered():
    import main
    paths = {r.path for r in main.finance_payee_router.routes}
    assert {"/finance/payee-accounts/me", "/finance/payee-requests/{request_id}/approve",
            "/finance/people/{employee_id}"} <= paths


# ---- fix round 1: duplicate rows / edit paths ----
def test_self_overwrites_every_duplicate_row():
    vals = _vals("SELF") + [_v(4, text="9999999999")]
    sr._guard_advance_values(_Db(MASTER), _form(), vals, "E1")
    assert [v.value_text for v in vals if v.question_id == 4] == ["1112223334", "1112223334"]


def test_duplicate_values_rejected():
    with pytest.raises(HTTPException) as ei:
        sr._reject_duplicate_values(_vals("SELF") + [_v(4, text="x")])
    assert ei.value.status_code == 400 and ei.value.detail == "ข้อมูลในฟอร์มซ้ำ กรุณาโหลดหน้าใหม่แล้วส่งอีกครั้ง"
    sr._reject_duplicate_values(_vals("SELF"))


def test_submit_and_update_call_duplicate_check():
    import inspect
    assert "_reject_duplicate_values" in inspect.getsource(sr.submit_form)
    assert "_reject_duplicate_values" in inspect.getsource(sr.update_form_details)


def test_update_to_self_persists_master_snapshot(monkeypatch):
    from datetime import date as _d
    monkeypatch.setattr(sr, "_advance_edit_state", lambda db, sub: (sr.advance_logic.PENDING_APPROVAL, False))
    form = _form()
    form.form_type = sr.ADVANCE_FORM_TYPE
    form.version = 1
    stored = [NS(question_id=1, value_text=None, value_number=100, value_date=None, value_boolean=None),
              NS(question_id=2, value_text="SUPPLIER", value_number=None, value_date=None, value_boolean=None),
              NS(question_id=3, value_text="SCB", value_number=None, value_date=None, value_boolean=None),
              NS(question_id=4, value_text="9999999999", value_number=None, value_date=None, value_boolean=None),
              NS(question_id=5, value_text="Attacker", value_number=None, value_date=None, value_boolean=None),
              NS(question_id=6, value_text=None, value_number=None, value_boolean=None,
                 value_date=_d.today() + timedelta(days=5))]
    for q in form.questions:
        q.is_required = False
    sub = NS(status="Open", status_approve="In Progress", created_by="E1", form=form, values=stored, id=9,
             form_id="F-1", form_master_id=1, updated_by=None)

    class Q:
        def __init__(self, result):
            self.result = result

        def options(self, *a):
            return self

        def filter(self, *a):
            return self

        def first(self):
            return self.result

    class Db:
        added = []

        def query(self, model):
            return Q(sub if model is sr.FormSubmission else MASTER)

        def add(self, o):
            self.added.append(o)

        def commit(self):
            pass

        def rollback(self):
            pass

    payload = NS(updated_by="E1", values=[NS(question_id=2, value_text="SELF", value_number=None,
                                              value_date=None, value_boolean=None)])
    db = Db()
    sr.update_form_details("F-1", payload, db)
    got = {r.question_id: r.value_text for r in stored}
    assert (got[2], got[3], got[4], got[5]) == ("SELF", "KBANK", "1112223334", "สมชาย")
    assert stored[0].value_number == 100 and stored[5].value_date is not None
