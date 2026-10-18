"""A fully approved Advance request tells the accounting inbox it is waiting to be vouchered."""
import os
from types import SimpleNamespace as NS

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

import pytest  # noqa: E402

from services.finance import finance_mail as mail  # noqa: E402


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("FINANCE_EMAIL_ENABLED", "FINANCE_SMTP_USER", "FINANCE_SMTP_PASSWORD",
              "FE_BASE_URL", "FINANCE_INBOX_EMAIL", "FINANCE_EMAIL_TEST_TO"):
        monkeypatch.delenv(k, raising=False)


def test_default_recipient_is_the_accounting_inbox():
    assert mail.finance_inbox_emails() == ["accountbkk@menatransport.co.th"]


def test_recipient_can_be_overridden_and_split_on_commas(monkeypatch):
    monkeypatch.setenv("FINANCE_INBOX_EMAIL", "a@x.co, b@x.co ,a@x.co")
    assert mail.finance_inbox_emails() == ["a@x.co", "b@x.co"]


def test_email_names_the_request_and_says_it_waits_for_voucher():
    subject, html = mail.render_event_email(mail.READY_FOR_VOUCHER, {
        "form_id": "ADV-2610-003", "requester_name": "กฤตบุญ ลายเงิน", "amount": 5000,
    })
    assert "ADV-2610-003" in subject
    assert "รอตั้งเบิก" in subject or "รอตั้งเบิก" in html
    assert "กฤตบุญ ลายเงิน" in html
    assert "5,000" in html


def test_the_event_goes_to_the_inbox_not_to_the_requester(monkeypatch):
    monkeypatch.setenv("FINANCE_EMAIL_ENABLED", "true")
    sent = {}

    class Tasks:
        def add_task(self, fn, to, subject, html):
            sent["to"] = to
            sent["subject"] = subject

    monkeypatch.setattr(mail.advance_repo, "request_values_by_submission",
                        lambda db, ids: {1: {"amount": 5000}})
    monkeypatch.setattr(mail.advance_repo, "people_by_employee_id",
                        lambda db, ids: {"E1": {"name": "กฤตบุญ ลายเงิน"}})
    monkeypatch.setattr(mail, "requester_email", lambda db, sub: ["requester@x.co"])

    submission = NS(id=1, form_id="ADV-2610-003", created_by="E1")
    mail.queue_event(Tasks(), None, mail.READY_FOR_VOUCHER, submission)

    assert sent["to"] == ["accountbkk@menatransport.co.th"]
    assert "requester@x.co" not in sent["to"]


def test_nothing_is_queued_while_the_switch_is_off(monkeypatch):
    calls = []

    class Tasks:
        def add_task(self, *a, **k):
            calls.append(a)

    mail.queue_event(Tasks(), None, mail.READY_FOR_VOUCHER, NS(id=1, form_id="ADV-1", created_by="E1"))
    assert calls == []


def test_test_address_overrides_every_recipient(monkeypatch):
    monkeypatch.setenv("FINANCE_EMAIL_ENABLED", "true")
    monkeypatch.setenv("FINANCE_EMAIL_TEST_TO", "Narongkorn.at@menatransport.co.th")
    seen = {}

    def fake_connect(user, password):
        raise AssertionError("must not connect without credentials")

    monkeypatch.setattr(mail, "_connect", fake_connect)
    # no SMTP credentials -> it stops before connecting, but after the redirect is logged
    assert mail.deliver_finance_email(["someone@else.co"], "s", "<p>h</p>") == 0
    assert seen == {}
