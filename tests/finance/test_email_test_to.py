"""FINANCE_EMAIL_TEST_TO: ช่วงทดสอบ อีเมลทุกฉบับเข้ากล่องเดียว ไม่หลุดถึงคนจริง (ผู้ใช้สั่ง 20/10/2026)."""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

import smtplib  # noqa: E402

import pytest  # noqa: E402

from services.finance import finance_mail as fm  # noqa: E402


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k in ("FINANCE_EMAIL_ENABLED", "FINANCE_SMTP_USER", "FINANCE_SMTP_PASSWORD", "FINANCE_EMAIL_TEST_TO"):
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


def test_everything_goes_to_the_test_box_once(monkeypatch, smtp):
    monkeypatch.setenv("FINANCE_EMAIL_TEST_TO", "tester@x.com")
    n = fm.deliver_finance_email(["a@x.com", "b@x.com"], "หัวข้อ", "<p>เนื้อหา</p>", cc=["acc@x.com"])
    assert n == 1
    assert [to for to, _ in smtp] == [["tester@x.com"]]
    body = smtp[0][1]
    assert "To: tester@x.com" in body and "Cc:" not in body


def test_the_test_mail_says_who_would_have_got_it(monkeypatch, smtp):
    monkeypatch.setenv("FINANCE_EMAIL_TEST_TO", "tester@x.com")
    fm.deliver_finance_email(["a@x.com"], "หัวข้อ", "<p>เนื้อหา</p>", cc=["acc@x.com"])
    import email
    msg = email.message_from_string(smtp[0][1])
    html = msg.get_payload()[0].get_payload(decode=True).decode("utf-8")
    assert "a@x.com" in html and "acc@x.com" in html and "เนื้อหา" in html


def test_blank_test_to_means_normal_delivery(monkeypatch, smtp):
    monkeypatch.setenv("FINANCE_EMAIL_TEST_TO", "  ")
    fm.deliver_finance_email(["a@x.com"], "s", "<p>h</p>")
    assert [to for to, _ in smtp] == [["a@x.com"]]


def test_switch_off_still_sends_nothing(monkeypatch, smtp):
    monkeypatch.setenv("FINANCE_EMAIL_TEST_TO", "tester@x.com")
    monkeypatch.setenv("FINANCE_EMAIL_ENABLED", "false")
    assert fm.deliver_finance_email(["a@x.com"], "s", "<p>h</p>") == 0
    assert smtp == []


def test_no_recipient_sends_nothing_even_in_test_mode(monkeypatch, smtp):
    monkeypatch.setenv("FINANCE_EMAIL_TEST_TO", "tester@x.com")
    assert fm.deliver_finance_email([], "s", "<p>h</p>") == 0
    assert smtp == []


def test_banner_sits_inside_the_body():
    html = fm._with_banner('<!DOCTYPE html><html><body style="x"><p>เนื้อหา</p></body></html>', "<div>B</div>")
    assert html.index("<div>B</div>") > html.index("<body") and html.startswith("<!DOCTYPE")
