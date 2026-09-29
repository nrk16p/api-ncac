import os
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

from services.finance import advance_repo as repo  # noqa: E402

SUB = SimpleNamespace(id=5, form_id="ADV-2026-0001", created_at=datetime(2026, 7, 7, 3, 0),
                      status_approve="Approved", created_by="670001")
PEOPLE = {"670001": {"employee_id": "670001", "name": "อรณภัชชา จัตุรัส", "department": "HR",
                     "site": "สำนักงานสระบุรี", "site_code": "สสบ."}}
REQUEST = {"purpose": "ค่าแอร์", "amount": Decimal("12740"), "use_date": datetime(2026, 7, 9)}


def _adv(**kw):
    base = dict(acc_code="110102", voucher_no="SADV2607-005", voucher_date=date(2026, 7, 7), payment_doc_no=None,
                purpose="ค่าแอร์", amount_paid=Decimal("12740.00"), transfer_date=date(2026, 7, 9),
                clear_due_date=date(2026, 7, 16), paid_by="680001", paid_at=datetime(2026, 7, 9, tzinfo=timezone.utc),
                clear_date=None, amount_actual=None, clear_doc_no=None, settle_amount=None, settle_date=None,
                remark=None, clear_submitted_at=None, review_remark=None, closed_by=None, closed_at=None,
                fin_status="PAID")
    base.update(kw)
    return SimpleNamespace(**base)


def test_awaiting_payment_without_fin_row():
    item = repo.serialize_advance(SUB, None, REQUEST, PEOPLE, {}, date(2026, 7, 8))
    assert item["status"] == "AWAITING_VOUCHER"
    assert item["status_label"] == "รอตั้งเบิกทำจ่าย"
    assert item["fin"] is None
    assert item["request"] == {"purpose": "ค่าแอร์", "amount": 12740.0, "use_date": "2026-07-09T00:00:00+00:00",
                              "cost_center": None, "bank": None, "bank_label": None, "account_no": None,
                              "account_name": None}
    assert item["created_at"] == "2026-07-07T03:00:00+00:00"
    assert item["requester"]["site_code"] == "สสบ."


def test_paid_overdue_with_account_name():
    item = repo.serialize_advance(SUB, _adv(), REQUEST, PEOPLE, {"110102": "เงินสดย่อย-สระบุรี"}, date(2026, 7, 17))
    assert (item["status"], item["overdue"]) == ("AWAITING_CLEARING", True)
    assert item["fin"]["acc_name"] == "เงินสดย่อย-สระบุรี"
    assert item["fin"]["amount_paid"] == 12740.0
    assert item["fin"]["clear_due_date"] == "2026-07-16"


def test_unknown_requester_falls_back_to_employee_id():
    item = repo.serialize_advance(SUB, None, None, {}, {}, date(2026, 7, 8))
    assert item["requester"] == {"employee_id": "670001", "name": None, "department": None,
                                 "site": None, "site_code": None}
    assert item["request"] == {"purpose": None, "amount": None, "use_date": None, "cost_center": None,
                              "bank": None, "bank_label": None, "account_no": None, "account_name": None}


def test_bkk_day_start_is_previous_utc_evening():
    assert repo.bkk_day_start_utc(date(2026, 7, 9)) == datetime(2026, 7, 8, 17, 0)


def test_serialize_request_v2_fields():
    from decimal import Decimal
    from services.finance.advance_repo import serialize_request
    out = serialize_request({"purpose": "p", "amount": Decimal("1500.50"), "use_date": None,
                             "cost_center": "ศลบ", "bank": "KBANK", "account_no": "1234567890",
                             "account_name": "นาย ก"})
    assert out == {"purpose": "p", "amount": 1500.5, "use_date": None, "cost_center": "ศลบ", "bank": "KBANK",
                   "bank_label": "ธนาคารกสิกรไทย", "account_no": "1234567890", "account_name": "นาย ก"}


def test_serialize_request_empty():
    from services.finance.advance_repo import serialize_request
    out = serialize_request(None)
    assert out["amount"] is None and out["bank_label"] is None and out["cost_center"] is None
