"""บั๊กจากรอบทดสอบทุกกรณี 30/10/2026 (flowrun/cases.cjs)

1. ค่าเดินทาง ด่านซ้ำอ่อน "คนเดิมใน 7 วัน" ไม่เคยเตือน — หน้าเปิดใบส่ง driver_name จากช่อง driver
   ซึ่งค่าเดินทางไม่มี (ผู้เดินทางอยู่ที่ payee_name) · BE จึงต้องถอยไปใช้ผู้รับเงินเมื่อไม่มีชื่อคนขับ
2. แผนกที่เปิดประเภทได้ (ค่าชั่ง B01 B02 B04 B05 · ยาง/ซ่อม B02) เคยคุมแค่ dropdown ฝั่งหน้าเว็บ
   ยิง API ตรงเปิดค่ายางบนวงเงินบัญชี A04 ได้ · ต้องกันที่ BE ด้วย
"""
from datetime import date

import pytest
from fastapi import HTTPException

from models.finance_petty_model import FinPettyExpenseType, FinPettyFund
from routes.finance import petty_routes as R
from schemas.finance_petty_schema import ClaimIn
from services.finance import petty_logic as L
from services.finance import petty_validate as V

TRAVEL_SPEC = {
    "item_based": False,
    "duplicate_check": {"hard": {"keys": ["ref_no", "truck_code", "truck_plate"], "days": 365},
                        "soft": {"keys": ["event_date", "driver"], "days": 7}},
}
ROW_SPEC = {"item_based": True,
            "item_fields": [{"key": "event_date", "label": "วันที่", "type": "date", "required": True},
                            {"key": "rate", "label": "ราคา", "type": "money", "required": True}]}


class TestTravelSoftDuplicateByTraveller:
    @pytest.fixture
    def travel(self, db_session, fund):
        db_session.add(FinPettyExpenseType(code="TRAVEL", name_th="ค่าเดินทางคนขับ", spec=TRAVEL_SPEC))
        db_session.commit()

    def _create(self, db_session, claim_payload, day, payee="สมศักดิ์ แก้วใส"):
        body = ClaimIn(**claim_payload(expense_type="TRAVEL", payee_name=payee,
                                       items=[{"event_date": date(2026, 10, day), "rate": 250}]))
        return R.create_claim(body, db=db_session)

    def _soft(self, db_session, form_id):
        claim = R.repo.get_claim(db_session, form_id)
        gate = R._gate(db_session, claim, R.repo.claim_items(db_session, claim.id), V.STAGE_APPROVE, {})
        return gate["duplicates"]["soft"]

    def test_same_traveller_within_7_days_warns(self, db_session, travel, claim_payload, seq_stub, frozen_bkk):
        first = self._create(db_session, claim_payload, 20)
        second = self._create(db_session, claim_payload, 23)
        assert [s["form_id"] for s in self._soft(db_session, second["form_id"])] == [first["form_id"]]

    def test_another_traveller_does_not_warn(self, db_session, travel, claim_payload, seq_stub, frozen_bkk):
        self._create(db_session, claim_payload, 20)
        second = self._create(db_session, claim_payload, 23, payee="ประยูร ทองคำ")
        assert self._soft(db_session, second["form_id"]) == []


class TestDivisionRuleOnTheServer:
    @pytest.fixture
    def types(self, db_session, fund):
        for code in ("TIRE", "TOLL"):  # WEIGH มีจาก fixture fund อยู่แล้ว
            db_session.add(FinPettyExpenseType(code=code, name_th=code, spec=ROW_SPEC))
        accounting = FinPettyFund(branch_code="03", division_code="A04", holder_employee_id="12399")
        db_session.add(accounting)
        db_session.commit()
        return {"B02": fund.id, "A04": accounting.id}

    def _create(self, db_session, claim_payload, code, fund_id):
        body = ClaimIn(**claim_payload(expense_type=code, fund_id=fund_id,
                                       items=[{"event_date": date(2026, 10, 20), "rate": 100}]))
        return R.create_claim(body, db=db_session)

    def test_the_map_matches_the_accounting_table(self):
        assert L.division_allowed("TIRE", "B02") and not L.division_allowed("TIRE", "A04")
        assert L.division_allowed("WEIGH", "B05") and not L.division_allowed("WEIGH", "A03")
        assert L.division_allowed("GENERAL", "A04")  # ไม่อยู่ในตาราง = ทุกแผนก

    def test_tire_on_an_accounting_fund_is_refused(self, db_session, types, claim_payload, seq_stub, frozen_bkk):
        with pytest.raises(HTTPException) as err:
            self._create(db_session, claim_payload, "TIRE", types["A04"])
        assert err.value.status_code == 422
        assert "A04" in str(err.value.detail)

    def test_tire_on_the_vehicle_fund_is_fine(self, db_session, types, claim_payload, seq_stub, frozen_bkk):
        assert self._create(db_session, claim_payload, "TIRE", types["B02"])["form_id"]

    def test_types_without_a_rule_open_anywhere(self, db_session, types, claim_payload, seq_stub, frozen_bkk):
        assert self._create(db_session, claim_payload, "TOLL", types["A04"])["form_id"]
