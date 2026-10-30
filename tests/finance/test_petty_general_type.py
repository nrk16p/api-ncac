"""ประเภท "ค่าใช้จ่ายทั่วไป" (GENERAL) — ผู้ใช้สั่ง 29/10/2026

เอกสารตามตารางของผู้ใช้: บิลเงินสด/ใบกำกับภาษี/ใบเสร็จรับเงิน (บิลจริงเท่านั้น) ·
ใบรับรองแทนใบเสร็จรับเงิน เฉพาะกรณีบิลเงินสดเป็นสำเนา · หลักฐานการโอน · DD ที่เหลือเหมือนประเภทอื่น
ใบทั่วไปส่วนใหญ่ไม่มีรถ ด่านซ้ำเข้มจึงใช้ เลขที่บิล + จ่ายให้ และเทียบกับใบอื่นเท่านั้น
(บิลใบเดียวมีหลายรายการ กรอกหลายแถวด้วยเลขบิลเดียวกันได้)
"""
import json
import pathlib
from datetime import date

import pytest

from models.finance_petty_model import FinPettyExpenseType
from routes.finance import petty_routes as R
from schemas.finance_petty_schema import ClaimIn, ExpenseTypeUpdate
from services.finance import petty_validate as V

SQL = pathlib.Path(__file__).resolve().parents[2] / "scripts/migrations/2026-10-30_petty_general_type.sql"


def _spec() -> dict:
    text = SQL.read_text(encoding="utf-8")
    body = text.split("-- spec:GENERAL")[1].split("-- /spec")[0].strip()
    return json.loads(body.strip().strip("'"))


@pytest.fixture(scope="module")
def spec():
    return _spec()


def _att(spec, key):
    return next(a for a in spec["attachments"] if a["key"] == key)


def _missing(spec, detail, attachments):
    out = V.validate_claim({"amount": 300, "detail": detail}, [], spec, V.STAGE_APPROVE, attachments)
    return [m["key"] for m in out["missing"]]


class TestTheRow:
    def test_inserted_with_its_thai_name(self):
        text = SQL.read_text(encoding="utf-8")
        assert "'GENERAL'" in text and "'ค่าใช้จ่ายทั่วไป'" in text

    def test_rerun_does_not_overwrite_edits_from_the_master_page(self):
        assert "ON CONFLICT (code) DO NOTHING" in SQL.read_text(encoding="utf-8")

    def test_passes_the_schema_validator(self, spec):
        assert ExpenseTypeUpdate(name_th="ค่าใช้จ่ายทั่วไป", spec=spec).spec is not None


class TestFields:
    def test_item_based_and_counts_in_items(self, spec):
        assert spec["item_based"] is True
        assert spec["print"]["unit"] == "รายการ"

    def test_each_row_needs_bill_date_number_what_qty_and_price(self, spec):
        required = [f["key"] for f in spec["item_fields"] if f.get("required")]
        assert required == ["event_date", "ref_no", "remark", "qty", "rate"]

    def test_truck_is_optional(self, spec):
        truck = next(f for f in spec["item_fields"] if f["key"] == "truck")
        assert truck.get("required") is False

    def test_header_asks_whether_the_bill_is_the_original_or_a_copy(self, spec):
        bill = next(f for f in spec["header_fields"] if f["key"] == "bill_kind")
        assert bill["options"] == ["ตัวจริง", "สำเนา"]
        assert bill["default"] == "ตัวจริง" and bill["required"] is True

    def test_same_payee_header_as_the_repair_type(self, spec):
        keys = {f["key"]: f for f in spec["header_fields"]}
        assert keys["payment_method"]["options"] == ["โอนเงิน", "เงินสด"]
        assert keys["payee_type"]["options"] == ["นิติบุคคล", "บุคคล/ร้านเงินสด"]


class TestDocuments:
    def test_only_the_three_documents_from_the_user_table(self, spec):
        assert [a["key"] for a in spec["attachments"]] == ["receipt", "receipt_substitute", "transfer"]

    def test_bill_is_required_and_must_be_the_original(self, spec):
        receipt = _att(spec, "receipt")
        assert receipt["label"] == "บิลเงินสด/ใบกำกับภาษี/ใบเสร็จรับเงิน"
        assert receipt["required"] is True and "บิลจริงเท่านั้น" in receipt["note"]

    def test_transfer_proof_is_required(self, spec):
        assert _att(spec, "transfer")["required"] is True

    def test_substitute_is_needed_when_the_bill_is_a_copy(self, spec):
        assert "receipt_substitute" in _missing(spec, {"bill_kind": "สำเนา"}, {"receipt": 1, "transfer": 1})

    def test_substitute_is_not_needed_for_an_original_bill(self, spec):
        assert _missing(spec, {"bill_kind": "ตัวจริง"}, {"receipt": 1, "transfer": 1}) == []

    def test_dd_from_500_baht_like_every_other_type(self, spec):
        rule = next(r for r in spec["rules"] if r.get("require_field") == "dd_no")
        assert rule == {"when": "total >= 500", "require_field": "dd_no", "at": "BEFORE_SUBMIT_TO_ACCOUNTING"}

    def test_substitute_print_shows_only_for_a_copy(self, spec):
        assert spec["prints"] == ["PETTY_VOUCHER", "RECEIPT_SUBSTITUTE", "DD"]
        assert spec["print_when"] == {"RECEIPT_SUBSTITUTE": "bill_kind == สำเนา"}


class TestDuplicateByBillAndPayee:
    def test_spec_keys_the_hard_check_on_bill_and_payee(self, spec):
        assert spec["duplicate_check"]["hard"]["keys"] == ["ref_no", "payee_name"]

    def _row(self, **over):
        row = {"event_date": date(2026, 10, 20), "ref_no": "001", "payee_name": "ร้านเครื่องเขียน ก.",
               "truck_code": None, "truck_plate": None}
        row.update(over)
        return row

    def test_same_bill_number_from_another_shop_is_not_a_duplicate(self, spec):
        prior = [self._row(payee_name="ร้านอะไหล่ ข.", form_id="LB-A04-2610-001")]
        assert V.split_duplicates([self._row()], prior, spec)["hard"] == []

    def test_same_bill_same_shop_on_another_claim_is_blocked(self, spec):
        prior = [self._row(payee_name="  ร้านเครื่องเขียน  ก. ", form_id="LB-A04-2610-001")]
        hard = V.split_duplicates([self._row()], prior, spec)["hard"]
        assert [h["form_id"] for h in hard] == ["LB-A04-2610-001"]
        assert "ผู้รับเงิน" in hard[0]["message"]

    def test_one_bill_on_several_rows_of_the_same_claim_is_fine(self, spec):
        rows = [self._row(), self._row(), self._row()]
        assert V.split_duplicates(rows, [], spec)["hard"] == []

    def test_other_types_still_key_on_bill_and_truck(self):
        tire = {"duplicate_check": {"hard": {"keys": ["ref_no", "truck_code", "truck_plate"]}}}
        prior = [self._row(payee_name="คนอื่น", truck_code="161", truck_plate="71-4285", form_id="X")]
        hard = V.split_duplicates([self._row(truck_code="161", truck_plate="71-4285")], prior, tire)["hard"]
        assert [h["form_id"] for h in hard] == ["X"]


class TestThroughTheRoutes:
    ROW = {"event_date": date(2026, 10, 20), "ref_no": "001", "remark": "กระดาษ A4", "qty": 2, "rate": 120}

    @pytest.fixture
    def general(self, db_session, fund, spec):
        db_session.add(FinPettyExpenseType(code="GENERAL", name_th="ค่าใช้จ่ายทั่วไป", spec=spec))
        db_session.commit()

    def _create(self, db_session, claim_payload, payee):
        body = ClaimIn(**claim_payload(expense_type="GENERAL", payee_name=payee, items=[self.ROW],
                                       detail={"bill_kind": "ตัวจริง", "payment_method": "เงินสด",
                                               "payee_type": "บุคคล/ร้านเงินสด"}))
        return R.create_claim(body, db=db_session)

    def test_live_check_uses_the_payee(self, db_session, general, claim_payload, seq_stub, frozen_bkk):
        first = self._create(db_session, claim_payload, "ร้านเครื่องเขียน ก.")
        same = R.duplicate_check(expense_type="GENERAL", event_date="2026-10-21", ref_no="001",
                                 payee_name="ร้านเครื่องเขียน ก.", db=db_session)
        other = R.duplicate_check(expense_type="GENERAL", event_date="2026-10-21", ref_no="001",
                                  payee_name="ร้านอะไหล่ ข.", db=db_session)
        assert [h["form_id"] for h in same["hard"]] == [first["form_id"]]
        assert other["hard"] == []

    def test_gate_compares_the_claims_payee(self, db_session, general, claim_payload, seq_stub, frozen_bkk):
        self._create(db_session, claim_payload, "ร้านเครื่องเขียน ก.")
        second = self._create(db_session, claim_payload, "ร้านอะไหล่ ข.")
        claim = R.repo.get_claim(db_session, second["form_id"])
        gate = R._gate(db_session, claim, R.repo.claim_items(db_session, claim.id), V.STAGE_APPROVE, {})
        assert gate["duplicates"]["hard"] == []
