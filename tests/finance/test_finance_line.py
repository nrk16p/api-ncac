# -*- coding: utf-8 -*-
"""แจ้งเตือนกลุ่มไลน์ของงาน Advance — ประตูแผนก สวิตช์ และหน้าตาข้อความ"""
import pytest

from services.finance import finance_line as fl

BASE = "https://menait-service.vercel.app"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("LINE_TOKEN", "FINANCE_LINE_TOKEN", "FINANCE_LINE_ENABLED", "FINANCE_LINE_GROUP_ID",
                "FINANCE_LINE_DEPARTMENT_IDS", "FE_BASE_URL"):
        monkeypatch.delenv(key, raising=False)


# ---------------------------- สวิตช์และกลุ่ม ----------------------------

def test_line_off_without_token():
    assert fl.line_enabled() is False


def test_line_on_with_token(monkeypatch):
    monkeypatch.setenv("FINANCE_LINE_TOKEN", "x")
    assert fl.line_enabled() is True


def test_it_service_token_is_not_borrowed(monkeypatch):
    """ใช้ env ของตัวเอง ไม่หยิบของแจ้งเตือน IT Service มาใช้ เพราะเป็นคนละบอท"""
    monkeypatch.setenv("LINE_TOKEN", "it-service-bot")
    assert fl.token() == ""
    assert fl.line_enabled() is False


def test_line_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("FINANCE_LINE_TOKEN", "x")
    monkeypatch.setenv("FINANCE_LINE_ENABLED", "false")
    assert fl.line_enabled() is False


def test_default_group_is_the_accounting_group():
    assert fl.group_id() == "C1a62beae8781d841fb849d1cc7ee48cc"


def test_group_can_be_overridden(monkeypatch):
    monkeypatch.setenv("FINANCE_LINE_GROUP_ID", "Cffff")
    assert fl.group_id() == "Cffff"


# ---------------------------- ประตูแผนก ----------------------------

def test_only_accounting_department_goes_to_the_group():
    assert fl.should_notify(6) is True       # แผนกบัญชี
    assert fl.should_notify(4) is False      # แผนกการเงิน
    assert fl.should_notify(None) is False
    assert fl.should_notify("ฝ่ายบัญชี") is False


def test_departments_can_be_widened(monkeypatch):
    monkeypatch.setenv("FINANCE_LINE_DEPARTMENT_IDS", "4,6")
    assert fl.should_notify(4) is True
    assert fl.should_notify(6) is True
    assert fl.should_notify(21) is False


# ---------------------------- หน้าตาข้อความ ----------------------------

def test_no_decorative_flourish():
    """ข้อความต้องเป็นทางการ ไม่มีลายดอกไม้หรือตัวอักษรหนาแบบยูนิโค้ด"""
    text = fl.render_line_message(fl.SUBMITTED, {"form_id": "A", "requester_name": "ก", "amount": 1})
    for mark in ("✿", "⸝", "⋆", "𝐀", "✨", "🌷", "👤"):
        assert mark not in text


def test_submitted_message():
    text = fl.render_line_message(fl.SUBMITTED, {
        "form_id": "ADV-2610-007", "requester_name": "ดารณี ฤทธิจีน", "department": "แผนกบัญชี",
        "amount": 25000, "purpose": "ค่าเดินทางต่างจังหวัด", "use_date": "2026-10-22",
        "fe_base_url": BASE})
    assert text.startswith("เบิกเงิน Advance · ใบคำขอเบิกใหม่")
    assert "ADV-2610-007" in text
    assert "ดารณี ฤทธิจีน (แผนกบัญชี)" in text
    assert "25,000.00 บาท" in text
    assert "22/10/2026" in text
    assert "สถานะ: รออนุมัติ" in text
    assert f"{BASE}/finance/advance/ADV-2610-007" in text


def test_ready_for_voucher_message():
    text = fl.render_line_message(fl.READY_FOR_VOUCHER, {
        "form_id": "ADV-2610-007", "requester_name": "ดารณี ฤทธิจีน", "department": "แผนกบัญชี",
        "amount": 25000, "use_date": "2026-10-22", "approver_name": "เจษฎา รองศักดิ์",
        "fe_base_url": BASE})
    assert "สถานะ: รอตั้งเบิก" in text
    assert "วันที่ใช้เงิน: 22/10/2026" in text   # บัญชีต้องรู้ว่าต้องจ่ายทันไหม
    assert "ผู้อนุมัติขั้นสุดท้าย: เจษฎา รองศักดิ์" in text


def test_clearing_approval_message():
    text = fl.render_line_message(fl.CLEARING_APPROVAL, {
        "form_id": "ADV-2610-007", "requester_name": "ดารณี ฤทธิจีน", "department": "แผนกบัญชี",
        "amount_paid": 25000, "amount_actual": 9500, "settle_amount": 15500,
        "step": 1, "total_steps": 2, "clause": "6.5", "fe_base_url": BASE})
    assert "สถานะ: รออนุมัติเคลียร์" in text
    assert "จ่ายไปแล้ว: 25,000.00 บาท" in text
    assert "ยอดใช้จริง: 9,500.00 บาท" in text
    assert "คืนบริษัท: 15,500.00 บาท" in text
    assert "ขั้นอนุมัติ: ขั้นที่ 1 จาก 2 (TOA ข้อ 6.5)" in text


def test_no_settle_line_when_nothing_to_return():
    text = fl.render_line_message(fl.CLEARING_APPROVAL, {
        "form_id": "ADV-2610-008", "requester_name": "ก", "amount_paid": 5000,
        "amount_actual": 5000, "settle_amount": 0, "step": 1, "total_steps": 1})
    assert "คืนบริษัท" not in text


def test_message_without_fe_base_url_has_no_link():
    text = fl.render_line_message(fl.SUBMITTED, {"form_id": "ADV-2610-009", "requester_name": "ก"})
    assert "http" not in text
    assert "ADV-2610-009" in text


def test_bad_fe_base_url_is_ignored():
    text = fl.render_line_message(fl.SUBMITTED, {"form_id": "A", "requester_name": "ก",
                                                 "fe_base_url": "javascript:alert(1)"})
    assert "javascript" not in text


# ---------------------------- ส่งจริงต้องไม่ทำให้คำขอพัง ----------------------------

class _Resp:
    def __init__(self, status_code=200, text=""):
        self.status_code, self.text = status_code, text


def test_send_line_never_raises(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("line down")

    monkeypatch.setattr(fl.requests, "post", boom)
    assert fl.send_line("hello") is False


def test_send_line_reports_a_rejection(monkeypatch):
    monkeypatch.setattr(fl.requests, "post", lambda *a, **k: _Resp(403, "not a member"))
    assert fl.send_line("hello") is False


def test_send_line_posts_to_the_group_with_its_own_token(monkeypatch):
    monkeypatch.setenv("FINANCE_LINE_TOKEN", "finance-bot")
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, json=json, headers=headers)
        return _Resp()

    monkeypatch.setattr(fl.requests, "post", fake_post)
    assert fl.send_line("hello") is True
    assert seen["url"] == "https://api.line.me/v2/bot/message/push"
    assert seen["json"]["to"] == "C1a62beae8781d841fb849d1cc7ee48cc"
    assert seen["json"]["messages"] == [{"type": "text", "text": "hello"}]
    assert seen["headers"]["Authorization"] == "Bearer finance-bot"


def test_queue_line_is_a_noop_while_the_switch_is_off():
    calls = []
    fl.queue_line(type("BT", (), {"add_task": lambda *a, **k: calls.append(a)})(), None,
                  fl.SUBMITTED, type("S", (), {"id": 1, "form_id": "A", "created_by": "x"})())
    assert calls == []


# ---- อนุมัติครบ รอตั้งเบิก ส่งเข้ากลุ่มทุกแผนก · ใบคำขอเบิกใหม่ เฉพาะแผนกบัญชี (ผู้ใช้สั่ง 20/10/2026) ----

class _BT:
    def __init__(self):
        self.tasks = []

    def add_task(self, fn, *a, **k):
        self.tasks.append((fn, a, k))


def _queue(monkeypatch, event, department_id):
    monkeypatch.setenv("FINANCE_LINE_TOKEN", "x")
    monkeypatch.setattr(fl, "_department_of", lambda db, eid: department_id)
    monkeypatch.setattr(fl.advance_repo, "request_values_by_submission", lambda db, ids: {1: {"amount": 5000}})
    monkeypatch.setattr(fl.advance_repo, "people_by_employee_id", lambda db, ids: {"E1": {"name": "กฤตบุญ"}})
    bt = _BT()
    fl.queue_line(bt, object(), event, type("S", (), {"id": 1, "form_id": "ADV-2610-007", "created_by": "E1"})())
    return bt.tasks


@pytest.mark.parametrize("dept", [6, 11, 4, None])
def test_ready_for_voucher_goes_to_the_group_from_any_department(monkeypatch, dept):
    tasks = _queue(monkeypatch, fl.READY_FOR_VOUCHER, dept)
    assert len(tasks) == 1 and "อนุมัติครบทุกขั้นแล้ว" in tasks[0][1][0]


@pytest.mark.parametrize("event", [fl.SUBMITTED, fl.CLEARING_APPROVAL])
def test_other_events_stay_accounting_only(monkeypatch, event):
    assert _queue(monkeypatch, event, 11) == []
    assert len(_queue(monkeypatch, event, 6)) == 1
