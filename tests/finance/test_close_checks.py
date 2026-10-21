"""ปิดรายการ (ผู้ใช้สั่ง 21/10/2026): ต้องติ๊กรับเอกสารตัวจริง + วันที่รับ · เลขที่เอกสารเคลียร์ 10 หลัก · เช็กเลขซ้ำ"""
import os
from datetime import date
from types import SimpleNamespace as NS

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

from services.finance import advance_logic as logic  # noqa: E402

TODAY = date(2026, 10, 21)


@pytest.mark.parametrize("raw,want", [("CL69100001", "CL69100001"), (" 6910000123 ", "6910000123")])
def test_clear_doc_no_10_characters_ok(raw, want):
    assert logic.check_clear_doc_no(raw) == want


@pytest.mark.parametrize("raw", [None, "", "   ", "CL6910001", "CL691000012", "CL-6910001", "เลข1234567"])
def test_clear_doc_no_rejected(raw):
    with pytest.raises(logic.AdvanceRuleError) as exc:
        logic.check_clear_doc_no(raw)
    assert "10 หลัก" in str(exc.value)


def test_original_received_required():
    with pytest.raises(logic.AdvanceRuleError) as exc:
        logic.check_original_received(None, TODAY)
    assert "รับเอกสารตัวจริง" in str(exc.value)


def test_original_received_not_in_future():
    with pytest.raises(logic.AdvanceRuleError):
        logic.check_original_received(date(2026, 10, 22), TODAY)
    assert logic.check_original_received(TODAY, TODAY) == TODAY


@pytest.mark.parametrize("field", ["voucher_no", "payment_doc_no", "clear_doc_no"])
def test_duplicate_check_fields_allowed(field):
    assert field in logic.DOC_NO_FIELDS


def test_duplicate_endpoint_finds_other_documents(monkeypatch):
    from routes.finance import advance_routes as r
    rows = [NS(form_id="ADV-2610-003"), NS(form_id="ADV-2610-007")]

    class Q:
        def filter(self, *a): return self
        def order_by(self, *a): return self
        def limit(self, n): return self
        def all(self): return rows

    out = r.duplicate_doc_no(field="clear_doc_no", value=" CL69100001 ", exclude_form_id="ADV-2610-011",
                             db=NS(query=lambda *a: Q()))
    assert out == {"field": "clear_doc_no", "value": "CL69100001", "form_ids": ["ADV-2610-003", "ADV-2610-007"]}


def test_duplicate_endpoint_rejects_unknown_field():
    from fastapi import HTTPException
    from routes.finance import advance_routes as r
    with pytest.raises(HTTPException) as exc:
        r.duplicate_doc_no(field="account_no", value="1", exclude_form_id=None, db=None)
    assert exc.value.status_code == 400


def test_duplicate_endpoint_blank_value_returns_nothing():
    from routes.finance import advance_routes as r
    assert r.duplicate_doc_no(field="voucher_no", value="  ", exclude_form_id=None, db=None)["form_ids"] == []
