"""v3 §7b overdue clearing reminder: pure logic, template, job no-op, manual endpoint (all DB-free)."""
import os
from datetime import date
from types import SimpleNamespace as NS

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

import smtplib  # noqa: E402

import pytest  # noqa: E402

from routes.finance import advance_routes as ar  # noqa: E402
from schemas.finance_schema import OverdueRemindIn  # noqa: E402
from services.finance import overdue_reminder as od  # noqa: E402

TODAY = date(2026, 10, 20)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("FINANCE_EMAIL_ENABLED", "FINANCE_SMTP_USER", "FINANCE_SMTP_PASSWORD", "FE_BASE_URL"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def no_smtp(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("SMTP constructed")
    monkeypatch.setattr(smtplib, "SMTP", boom)


def test_due_edges():
    due = date(2026, 10, 14)  # day 6
    assert not od.due_for_reminder(TODAY, date(2026, 10, 15), None)  # 5 days
    assert not od.due_for_reminder(date(2026, 10, 20), date(2026, 10, 14), None)  # 6 days
    assert od.due_for_reminder(date(2026, 10, 21), due, None)  # 7 days
    assert not od.due_for_reminder(TODAY, date(2026, 9, 1), date(2026, 10, 14))  # reminded 6 days ago
    assert od.due_for_reminder(TODAY, date(2026, 9, 1), date(2026, 10, 13))  # 7 days ago
    assert od.due_for_reminder(TODAY, date(2026, 9, 1), None)


def test_thai_date():
    assert od.thai_long_date(date(2026, 10, 12)) == "12 ตุลาคม 2569"
    assert od.thai_long_date("2026-01-05") == "5 มกราคม 2569"


def _ctx(**kw):
    return {"firstname": "สมชาย", "lastname": "ใจดี", "form_id": "ADV2610-001", "amount_paid": 5000,
            "clear_due_date": date(2026, 10, 12), **kw}


def test_render_template():
    subject, html = od.render_overdue_email(_ctx())
    assert subject == "แจ้งเตือน/ติดตาม ยอดเงินยืมทดรองจ่ายเกินกำหนดชำระ สมชาย ใจดี"
    for text in ("เรียน คุณ สมชาย,",
                 "จากใบคำขอเบิกเงิน ADV2610-001 จำนวน 5,000.00 บาท ซึ่งได้ครบกำหนดชำระและส่งมอบใบเสร็จเพื่อเคลียร์ค่าใช้จ่าย "
                 "เมื่อวันที่ 12 ตุลาคม 2569 ที่ผ่านมา",
                 "ทางฝ่ายบัญชีจึงขอความกรุณาคุณ สมชาย ช่วยดำเนินการอย่างใดอย่างหนึ่งดังต่อไปนี้:",
                 "กรณีนำไปใช้จ่ายแล้ว:", "กรณีมีเงินคงเหลือหรือไม่ได้ใช้จ่าย:",
                 "หากคุณ สมชาย มีข้อติดขัด", "accountbkk@menatransport.co.th ค่ะ",
                 "ขอขอบคุณสำหรับความร่วมมือค่ะ"):
        assert text in html
    assert html.count("<li>") == 2
    assert "เปิดรายการในระบบ" not in html


def test_render_escapes_and_link(monkeypatch):
    _, html = od.render_overdue_email(_ctx(firstname="<b>x</b>", fe_base_url="https://fe.example.com"))
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html
    assert 'href="https://fe.example.com/finance/advance/ADV2610-001"' in html and "เปิดรายการในระบบ" in html
    _, bad = od.render_overdue_email(_ctx(fe_base_url="javascript:alert(1)"))
    assert "href" not in bad


def test_job_noop_when_disabled(no_smtp):
    def factory():
        raise AssertionError("DB touched")
    od.run_overdue_reminders(factory)  # returns, no queries, no SMTP


def test_job_never_raises(monkeypatch):
    monkeypatch.setenv("FINANCE_EMAIL_ENABLED", "true")

    def factory():
        raise RuntimeError("db down")
    od.run_overdue_reminders(factory)


class FakeDB:
    def __init__(self):
        self.added, self.commits = [], 0

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


def _item(form_id="A1", email="a@x.com", last=None, due=date(2026, 10, 1)):
    return {"form_id": form_id, "advance_id": 7, "firstname": "ก", "lastname": "ข", "email": email,
            "amount_paid": 100, "clear_due_date": due, "last_reminded": last}


def test_send_for_items_logs_only_on_success():
    db = FakeDB()
    items = [_item("A1"), _item("A2", email=None), _item("A3")]
    sent, skipped = od.send_for_items(db, items, "auto", "system", send=lambda i: i["form_id"] != "A3")
    assert sent == 1 and db.commits == 1
    assert [r.action for r in db.added] == ["OVERDUE_REMIND"]
    assert db.added[0].remark == "auto" and db.added[0].action_by == "system"
    assert {s["form_id"] for s in skipped} == {"A2", "A3"}


def test_job_sends_only_due(monkeypatch):
    monkeypatch.setenv("FINANCE_EMAIL_ENABLED", "true")
    db = FakeDB()
    db.close = lambda: None
    items = [_item("A1", last=None), _item("A2", last=date(2026, 10, 18)), _item("A3", due=date(2026, 10, 18))]
    monkeypatch.setattr(od, "load_overdue", lambda d, f=None: (items, TODAY))
    sent_ids = []
    monkeypatch.setattr(od, "send_for_items",
                        lambda d, its, r, a, send=None: (sent_ids.extend(i["form_id"] for i in its), (0, []))[1])
    od.run_overdue_reminders(lambda: db)
    assert sent_ids == ["A1"]


# ---- endpoint ----

def _endpoint(monkeypatch, enabled, items=()):
    if enabled:
        monkeypatch.setenv("FINANCE_EMAIL_ENABLED", "true")
    monkeypatch.setattr(ar, "require_finance", lambda db, emp: NS(employee_id=emp))
    monkeypatch.setattr(od, "load_overdue", lambda db, ids=None: (
        [i for i in items if ids is None or i["form_id"] in ids], TODAY))


def test_endpoint_disabled(monkeypatch, no_smtp):
    _endpoint(monkeypatch, False)
    db = FakeDB()
    out = ar.remind_overdue(OverdueRemindIn(action_by="F1"), db)
    assert out == {"sent": 0, "skipped": [], "disabled": True} and db.added == []


def test_endpoint_sent_and_skipped(monkeypatch):
    _endpoint(monkeypatch, True, [_item("A1"), _item("A2", last=date(2026, 10, 19))])
    monkeypatch.setattr(od, "send_one", lambda i: True)
    db = FakeDB()
    out = ar.remind_overdue(OverdueRemindIn(action_by="F1", form_ids=["A1", "A9"]), db)
    assert out == {"sent": 1, "skipped": [{"form_id": "A9", "reason": "ไม่ได้เกินกำหนดเคลียร์"}], "disabled": False}
    assert db.added[0].remark == "manual" and db.added[0].action_by == "F1"


def test_endpoint_all_ignores_interval(monkeypatch):
    _endpoint(monkeypatch, True, [_item("A1", last=date(2026, 10, 19)), _item("A2", email=None)])
    monkeypatch.setattr(od, "send_one", lambda i: True)
    out = ar.remind_overdue(OverdueRemindIn(action_by="F1"), FakeDB())
    assert out["sent"] == 1 and out["skipped"] == [{"form_id": "A2", "reason": "ผู้เบิกไม่มีอีเมล"}]
