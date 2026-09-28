import os
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

from routes.finance import advance_routes as r  # noqa: E402


def test_snapshot_of_missing_row_is_empty():
    assert r._snapshot(None, r.PAY_FIELDS) == {}


def test_snapshot_reads_listed_fields():
    adv = SimpleNamespace(clear_date=date(2026, 7, 15), amount_actual=Decimal("800"), clear_doc_no="R1",
                          settle_amount=Decimal("200"), settle_date=None, remark=None)
    assert r._snapshot(adv, r.CLEAR_FIELDS)["amount_actual"] == Decimal("800")


def test_pay_values_mapping():
    body = SimpleNamespace(acc_code="110103", voucher_no="SADV2607-005", voucher_date=date(2026, 7, 7),
                           payment_doc_no="PV1", purpose="x", amount_paid=Decimal("1000"),
                           transfer_date=date(2026, 7, 9))
    values = r._pay_values(body, date(2026, 7, 16))
    assert values["clear_due_date"] == date(2026, 7, 16)
    assert set(values) == set(r.PAY_FIELDS)
