"""พจส./พจร. ต่อแถว ตำแหน่งยาง และเลขที่ใบรับสภาพหนี้ (ผู้ใช้สั่ง 29/10/2026)

- ใบที่กรอกเป็นแถว: ทุกแถวเลือก พจส. หรือ พจร. · ใบเดี่ยวเลือกที่หัวใบ (detail.driver_role)
- ใบยาง: ตำแหน่งยางต่อแถว เลือกได้หลายตำแหน่ง รหัสเดียวกับ WMS (F1–F4 · RA1–RA8 · RB1–RB13)
- มี พจร. ในใบ (ทุกประเภท) → เจ้าของเงินสดต้องใส่เลขที่ใบรับสภาพหนี้ตอนอนุมัติ อ้างอิงแค่เลขเอกสาร
"""
from datetime import date

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from models.finance_petty_model import FinPettyExpenseType
from routes.finance import petty_routes as R
from schemas.finance_petty_schema import ApproveIn, ClaimItemIn
from services.finance import petty_repo as repo
from services.finance import petty_validate as V

TIRE_SPEC = {
    "item_based": True,
    "item_fields": [
        {"key": "event_date", "label": "วันที่ตามบิล", "type": "date", "required": True},
        {"key": "driver_role", "label": "พจส./พจร.", "type": "select", "options": ["พจส.", "พจร."],
         "required": True, "default": "พจส."},
        {"key": "driver_name", "label": "ชื่อ พจส./พจร.", "type": "text", "required": False},
        {"key": "tire_position", "label": "ตำแหน่งยาง", "type": "tire_positions", "required": True},
        {"key": "qty", "label": "จำนวน (เส้น)", "type": "number", "required": True},
        {"key": "rate", "label": "ราคา/เส้น", "type": "money", "required": True},
    ],
    "attachments": [{"key": "transfer", "label": "หลักฐานโอนเงิน", "required": True}],
}
PAID = {"transfer": 1}


@pytest.fixture
def tire(db_session, fund):
    db_session.add(FinPettyExpenseType(code="TIRE", name_th="ค่าปะ-เปลี่ยนยาง", spec=TIRE_SPEC))
    db_session.commit()


def _row(**over):
    row = {"event_date": date(2026, 10, 20), "truck_code": "161", "truck_plate": "71-4285", "ref_no": "B-1",
           "qty": 2, "rate": 600, "driver_role": "พจส.", "driver_name": "สมชาย", "tire_position": "RA1,RA2"}
    row.update(over)
    return row


def _open(db_session, claim_payload, *rows, **over):
    return repo.create_claim(db_session, claim_payload(expense_type="TIRE", **over), list(rows), actor="55555")


class TestStoredPerRow:
    def test_role_and_positions_come_back(self, db_session, tire, claim_payload, seq_stub, frozen_bkk):
        claim = _open(db_session, claim_payload, _row(driver_role="พจร.", tire_position="F1,RA1,RB13"))
        item = R.get_claim(claim.form_id, db=db_session)["items"][0]
        assert item["driver_role"] == "พจร." and item["tire_position"] == "F1,RA1,RB13"

    def test_schema_takes_only_the_two_roles(self):
        assert ClaimItemIn(event_date=date(2026, 10, 20), rate=1, driver_role="พจร.").driver_role == "พจร."
        with pytest.raises(ValidationError):
            ClaimItemIn(event_date=date(2026, 10, 20), rate=1, driver_role="คนขับ")

    def test_schema_takes_only_wms_tire_positions(self):
        row = ClaimItemIn(event_date=date(2026, 10, 20), rate=1, tire_position=" ra1, RA2 ,f3 ")
        assert row.tire_position == "RA1,RA2,F3"
        with pytest.raises(ValidationError):
            ClaimItemIn(event_date=date(2026, 10, 20), rate=1, tire_position="RA1,ZZ9")

    def test_blank_role_and_position_are_none(self):
        row = ClaimItemIn(event_date=date(2026, 10, 20), rate=1, driver_role="", tire_position="")
        assert row.driver_role is None and row.tire_position is None


class TestDebtDocNeeded:
    def test_any_pjr_row_needs_it(self):
        assert V.needs_debt_doc([{"driver_role": "พจส."}, {"driver_role": "พจร."}], {}) is True

    def test_pjs_only_does_not(self):
        assert V.needs_debt_doc([{"driver_role": "พจส."}, {"driver_role": None}], {}) is False

    def test_single_claims_use_the_header(self):
        assert V.needs_debt_doc([{"driver_role": None}], {"driver_role": "พจร."}) is True


class TestApprovalAsksForTheDebtDoc:
    def test_pjr_without_the_number_is_blocked(self, db_session, tire, claim_payload, seq_stub, frozen_bkk):
        claim = _open(db_session, claim_payload, _row(driver_role="พจร."))
        with pytest.raises(HTTPException) as exc:
            R.approve_claim(claim.form_id, ApproveIn(employee_id="12345", attachments=PAID), db_session)
        assert exc.value.status_code == 403
        assert any(m["key"] == "debt_doc_no" for m in exc.value.detail["missing"])

    def test_the_number_is_kept(self, db_session, tire, claim_payload, seq_stub, frozen_bkk):
        claim = _open(db_session, claim_payload, _row(driver_role="พจร."))
        out = R.approve_claim(claim.form_id, ApproveIn(employee_id="12345", attachments=PAID,
                                                       debt_doc_no=" RD-2610-015 "), db_session)
        assert out["status"] == "APPROVED" and out["debt_doc_no"] == "RD-2610-015"

    def test_pjs_needs_no_number(self, db_session, tire, claim_payload, seq_stub, frozen_bkk):
        claim = _open(db_session, claim_payload, _row())
        out = R.approve_claim(claim.form_id, ApproveIn(employee_id="12345", attachments=PAID), db_session)
        assert out["status"] == "APPROVED" and out["debt_doc_no"] is None

    def test_header_level_pjr_on_a_single_claim(self, db_session, tire, claim_payload, seq_stub, frozen_bkk):
        claim = _open(db_session, claim_payload, _row(driver_role=None), detail={"driver_role": "พจร."})
        with pytest.raises(HTTPException) as exc:
            R.approve_claim(claim.form_id, ApproveIn(employee_id="12345", attachments=PAID), db_session)
        assert any(m["key"] == "debt_doc_no" for m in exc.value.detail["missing"])


class TestRequiredOnEntry:
    def test_tyre_row_without_a_position_is_refused(self, db_session, tire, claim_payload, seq_stub, frozen_bkk):
        with pytest.raises(HTTPException) as exc:
            R._reject_incomplete_rows(db_session, "TIRE", [_row(tire_position=None)])
        assert any(m["key"] == "tire_position" for m in exc.value.detail["missing"])
