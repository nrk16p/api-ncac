"""ศูนย์และแผนกของผู้เบิก (ผู้ใช้สั่ง 29/10/2026)

หน้าเปิดใบเลือกจาก dropdown 43 รหัส (1400 … 3B07) · ใบสำคัญจ่ายพิมพ์รหัสนี้ในช่อง "รหัสค่าใช้จ่ายของผู้เบิก (บัญชี)"
BE เก็บรหัสในคอลัมน์ cost_center และรับเฉพาะรหัสในรายการ · ใบเก่า/ช่องทางอื่นที่ไม่ส่งมา = ว่าง
"""
from datetime import date

import pytest
from pydantic import ValidationError

from models.finance_petty_model import FinPettyExpenseType
from routes.finance import petty_routes as R
from schemas.finance_petty_schema import ClaimIn
from services.finance import petty_cost_centers as CC
from services.finance import petty_repo as repo

SPEC = {
    "item_based": True,
    "item_fields": [
        {"key": "event_date", "label": "วันที่", "type": "date", "required": True},
        {"key": "qty", "label": "จำนวน", "type": "number", "required": True},
        {"key": "rate", "label": "ราคา", "type": "money", "required": True},
    ],
}
ROW = {"event_date": date(2026, 10, 20), "truck_code": "161", "truck_plate": "71-4285", "ref_no": "B-1",
       "qty": 1, "rate": 150}


@pytest.fixture
def weigh(db_session, fund):
    db_session.add(FinPettyExpenseType(code="CCTEST", name_th="ประเภททดสอบ", spec=SPEC))
    db_session.commit()


def _payload(claim_payload, **over):
    return ClaimIn(**claim_payload(expense_type="CCTEST", items=[ROW], **over))


class TestTheList:
    def test_has_all_43_codes_from_the_user(self):
        assert len(CC.COST_CENTERS) == 43
        assert CC.COST_CENTERS["1400"] == "สกท - ขาย"
        assert CC.COST_CENTERS["2B02"] == "สสบ - ยานยนต์"
        assert CC.COST_CENTERS["3B07"] == "ศลบ. - Business Development"


class TestSchema:
    def test_known_code_passes(self, claim_payload):
        assert _payload(claim_payload, cost_center="3B02").cost_center == "3B02"

    def test_spaces_are_trimmed(self, claim_payload):
        assert _payload(claim_payload, cost_center=" 3B02 ").cost_center == "3B02"

    def test_unknown_code_is_rejected(self, claim_payload):
        with pytest.raises(ValidationError, match="ศูนย์และแผนก"):
            _payload(claim_payload, cost_center="9Z99")

    def test_missing_is_allowed_for_old_clients(self, claim_payload):
        assert _payload(claim_payload).cost_center is None

    def test_blank_counts_as_missing(self, claim_payload):
        assert _payload(claim_payload, cost_center="  ").cost_center is None


class TestStoredAndReturned:
    def test_create_keeps_the_code(self, db_session, weigh, claim_payload, seq_stub, frozen_bkk):
        out = R.create_claim(_payload(claim_payload, cost_center="3B02"), db=db_session)
        assert out["cost_center"] == "3B02"
        assert R.get_claim(out["form_id"], db=db_session)["cost_center"] == "3B02"

    def test_list_returns_it(self, db_session, weigh, claim_payload, seq_stub, frozen_bkk):
        out = R.create_claim(_payload(claim_payload, cost_center="2A04"), db=db_session)
        rows = {c["form_id"]: c for c in R.list_claims(limit=50, offset=0, db=db_session)["items"]}
        assert rows[out["form_id"]]["cost_center"] == "2A04"

    def test_edit_can_change_it(self, db_session, weigh, claim_payload, seq_stub, frozen_bkk):
        out = R.create_claim(_payload(claim_payload, cost_center="3B02"), db=db_session)
        edited = R.update_claim(out["form_id"], _payload(claim_payload, cost_center="3B04"), db=db_session)
        assert edited["cost_center"] == "3B04"

    def test_edit_without_it_keeps_the_old_code(self, db_session, weigh, claim_payload, seq_stub, frozen_bkk):
        """FE เก่าที่ยังไม่รู้จักช่องนี้ ส่งแก้ใบมา ต้องไม่ล้างรหัสทิ้ง"""
        out = R.create_claim(_payload(claim_payload, cost_center="3B02"), db=db_session)
        edited = R.update_claim(out["form_id"], _payload(claim_payload), db=db_session)
        assert edited["cost_center"] == "3B02"

    def test_old_claims_come_back_empty(self, db_session, weigh, claim_payload, seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, claim_payload(expense_type="CCTEST"), [ROW], actor="55555")
        assert R.get_claim(claim.form_id, db=db_session)["cost_center"] is None
