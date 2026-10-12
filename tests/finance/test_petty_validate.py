from datetime import date

from services.finance import petty_validate as V

WEIGH_SPEC = {
    "item_based": True,
    "item_fields": [
        {"key": "event_date", "label": "วันที่ชั่ง", "required": True},
        {"key": "ref_no", "label": "เลขที่บัตรชั่ง", "required": True},
        {"key": "rate", "label": "ราคา/ครั้ง", "required": True},
    ],
    "attachments": [
        {"key": "weigh_slip", "label": "ใบชั่งน้ำหนัก (สำเนา)", "required": True},
        {"key": "transfer", "label": "หลักฐานโอนเงิน", "required": True},
    ],
    "rules": [
        {"when": "total >= 500", "require_field": "dd_no", "at": "BEFORE_SUBMIT_TO_ACCOUNTING"},
        {"when": "always", "require_attachment": "weigh_slip", "at": "BEFORE_APPROVE"},
    ],
    "duplicate_check": {
        "scope": "item",
        "hard": {"keys": ["ref_no", "truck_code", "truck_plate"], "days": 365, "action": "block"},
        "soft": {"keys": ["event_date", "truck"], "days": 7, "action": "warn"},
    },
}

CLAIM = {"amount": 1640, "dd_no": None, "expense_type": "WEIGH"}
ITEM = {"event_date": date(2026, 9, 17), "truck_code": "161", "truck_plate": "71-4285",
        "ref_no": "000055", "qty": 1, "rate": 100}
FULL_FILES = {"weigh_slip": 16, "transfer": 1}


class TestDocumentGate:
    def test_complete_claim_passes(self):
        out = V.validate_claim(CLAIM, [ITEM], WEIGH_SPEC, V.STAGE_APPROVE, FULL_FILES)
        assert out["missing"] == []

    def test_missing_weigh_slip_blocks(self):
        out = V.validate_claim(CLAIM, [ITEM], WEIGH_SPEC, V.STAGE_APPROVE, {"transfer": 1})
        assert any("ใบชั่งน้ำหนัก" in m["label"] for m in out["missing"])

    def test_missing_transfer_proof_blocks(self):
        out = V.validate_claim(CLAIM, [ITEM], WEIGH_SPEC, V.STAGE_APPROVE, {"weigh_slip": 2})
        assert any(m["key"] == "transfer" for m in out["missing"])

    def test_row_without_ticket_number_blocks_and_names_the_row(self):
        items = [ITEM, dict(ITEM, ref_no="")]
        out = V.validate_claim(CLAIM, items, WEIGH_SPEC, V.STAGE_APPROVE, FULL_FILES)
        missing = [m for m in out["missing"] if m["key"] == "ref_no"]
        assert missing and missing[0]["line_no"] == 2

    def test_dd_is_not_required_at_approval(self):
        out = V.validate_claim(CLAIM, [ITEM], WEIGH_SPEC, V.STAGE_APPROVE, FULL_FILES)
        assert all(m["key"] != "dd_no" for m in out["missing"])

    def test_dd_required_at_submit_when_total_is_500_or_more(self):
        out = V.validate_claim(CLAIM, [ITEM], WEIGH_SPEC, V.STAGE_SUBMIT_ACC, FULL_FILES)
        assert any(m["key"] == "dd_no" for m in out["missing"])

    def test_dd_not_required_under_500(self):
        out = V.validate_claim(dict(CLAIM, amount=400), [ITEM], WEIGH_SPEC, V.STAGE_SUBMIT_ACC, FULL_FILES)
        assert all(m["key"] != "dd_no" for m in out["missing"])

    def test_dd_present_satisfies_the_rule(self):
        out = V.validate_claim(dict(CLAIM, dd_no="LBDD26090931"), [ITEM], WEIGH_SPEC,
                               V.STAGE_SUBMIT_ACC, FULL_FILES)
        assert out["missing"] == []

    def test_empty_spec_blocks_nothing(self):
        assert V.validate_claim(CLAIM, [ITEM], {}, V.STAGE_APPROVE, {})["missing"] == []

    def test_unknown_rule_condition_does_not_block(self):
        spec = {"rules": [{"when": "phase_of_moon == full", "require_field": "dd_no",
                           "at": "BEFORE_APPROVE"}]}
        assert V.validate_claim(CLAIM, [ITEM], spec, V.STAGE_APPROVE, {})["missing"] == []

    def test_each_missing_thing_is_listed_once(self):
        items = [dict(ITEM, ref_no=""), dict(ITEM, ref_no="")]
        out = V.validate_claim(CLAIM, items, WEIGH_SPEC, V.STAGE_APPROVE, {})
        slips = [m for m in out["missing"] if m["key"] == "weigh_slip"]
        assert len(slips) == 1


class TestDuplicates:
    def test_same_ticket_same_truck_is_hard(self):
        prior = [dict(ITEM, form_id="03-B02-2609-001")]
        out = V.split_duplicates([ITEM], prior, WEIGH_SPEC)
        assert len(out["hard"]) == 1
        assert out["hard"][0]["form_id"] == "03-B02-2609-001"

    def test_plate_written_with_a_space_still_matches(self):
        prior = [dict(ITEM, truck_plate="71 4285", form_id="03-B02-2609-001")]
        assert len(V.split_duplicates([ITEM], prior, WEIGH_SPEC)["hard"]) == 1

    def test_same_ticket_different_truck_is_not_hard(self):
        prior = [dict(ITEM, truck_code="999", truck_plate="99-9999", form_id="03-B02-2609-001")]
        assert V.split_duplicates([ITEM], prior, WEIGH_SPEC)["hard"] == []

    def test_two_rows_in_one_claim_with_the_same_ticket_is_hard(self):
        rows = [ITEM, dict(ITEM)]
        out = V.split_duplicates(rows, [], WEIGH_SPEC)
        assert len(out["hard"]) == 1 and out["hard"][0]["form_id"] is None

    def test_the_real_1093_case_different_tickets_is_only_soft(self):
        rows = [dict(ITEM, truck_code="1093", truck_plate="71-1093", ref_no="000120"),
                dict(ITEM, truck_code="1093", truck_plate="71-1093", ref_no="000121")]
        out = V.split_duplicates(rows, [], WEIGH_SPEC)
        assert out["hard"] == [] and len(out["soft"]) == 1

    def test_soft_window_is_seven_days(self):
        prior = [dict(ITEM, ref_no="999999", event_date=date(2026, 9, 23), form_id="x")]
        near = V.split_duplicates([dict(ITEM, event_date=date(2026, 9, 17))], prior, WEIGH_SPEC)
        far = V.split_duplicates([dict(ITEM, event_date=date(2026, 9, 1))], prior, WEIGH_SPEC)
        assert len(near["soft"]) == 1 and far["soft"] == []

    def test_rows_without_a_ticket_number_fall_back_to_soft_only(self):
        rows = [dict(ITEM, ref_no=""), dict(ITEM, ref_no="")]
        out = V.split_duplicates(rows, [], WEIGH_SPEC)
        assert out["hard"] == [] and len(out["soft"]) == 1

    def test_a_different_truck_on_the_same_day_is_not_flagged(self):
        rows = [ITEM, dict(ITEM, truck_code="144", truck_plate="71-9999", ref_no="000056")]
        out = V.split_duplicates(rows, [], WEIGH_SPEC)
        assert out["hard"] == [] and out["soft"] == []

    def test_hard_wins_over_soft_for_the_same_row(self):
        prior = [dict(ITEM, form_id="03-B02-2609-001")]
        out = V.split_duplicates([ITEM], prior, WEIGH_SPEC)
        assert len(out["hard"]) == 1 and out["soft"] == []


class TestFindPriorItems:
    """query ของด่านซ้ำต้องมีขอบเขตเสมอ — cluster/ตารางจะโตขึ้นเรื่อย ๆ"""

    def _claim(self, db_session, repo, claim_payload, weigh_items, **over):
        return repo.create_claim(db_session, claim_payload(**over), weigh_items, actor="55555")

    def test_finds_an_earlier_claim_with_the_same_ticket(self, db_session, fund, claim_payload,
                                                         weigh_items, seq_stub, frozen_bkk):
        from services.finance import petty_repo as repo
        first = self._claim(db_session, repo, claim_payload, weigh_items)
        prior = repo.find_prior_items(db_session, expense_type="WEIGH", keys=weigh_items,
                                      days=7, exclude_claim_id=None)
        assert any(p["form_id"] == first.form_id for p in prior)

    def test_excludes_the_claim_being_checked(self, db_session, fund, claim_payload, weigh_items,
                                              seq_stub, frozen_bkk):
        from services.finance import petty_repo as repo
        first = self._claim(db_session, repo, claim_payload, weigh_items)
        prior = repo.find_prior_items(db_session, expense_type="WEIGH", keys=weigh_items,
                                      days=7, exclude_claim_id=first.id)
        assert prior == []

    def test_ignores_other_expense_types(self, db_session, fund, claim_payload, weigh_items,
                                         seq_stub, frozen_bkk):
        from models.finance_petty_model import FinPettyExpenseType
        from services.finance import petty_repo as repo
        db_session.add(FinPettyExpenseType(code="TIRE", name_th="ค่าปะยาง", spec={}))
        db_session.commit()
        self._claim(db_session, repo, claim_payload, weigh_items)
        prior = repo.find_prior_items(db_session, expense_type="TIRE", keys=weigh_items,
                                      days=7, exclude_claim_id=None)
        assert prior == []

    def test_ignores_returned_claims(self, db_session, fund, claim_payload, weigh_items,
                                     seq_stub, frozen_bkk):
        from services.finance import petty_logic as PL
        from services.finance import petty_repo as repo
        claim = self._claim(db_session, repo, claim_payload, weigh_items)
        repo.set_status(db_session, claim, PL.RETURNED, "12345", "RETURN")
        prior = repo.find_prior_items(db_session, expense_type="WEIGH", keys=weigh_items,
                                      days=7, exclude_claim_id=None)
        assert prior == []

    def test_no_keys_means_no_query(self, db_session, fund):
        from services.finance import petty_repo as repo
        assert repo.find_prior_items(db_session, expense_type="WEIGH", keys=[], days=7,
                                     exclude_claim_id=None) == []

    def test_rows_outside_the_window_are_not_returned(self, db_session, fund, claim_payload,
                                                      weigh_items, seq_stub, frozen_bkk):
        from datetime import date
        from services.finance import petty_repo as repo
        self._claim(db_session, repo, claim_payload, weigh_items)
        far = [dict(weigh_items[0], event_date=date(2026, 1, 1))]
        prior = repo.find_prior_items(db_session, expense_type="WEIGH", keys=far, days=7,
                                      exclude_claim_id=None)
        assert prior == []


class TestItemFieldsThatLiveInTwoColumns:
    """spec ของจริงบังคับช่อง `truck` `truck_type` `remark` — ด่านต้องอ่านให้ตรงกับที่เก็บจริง

    เจอจากการซ้อมใช้งาน 2026-10-13: ด่านเคยบล็อกทุกใบว่า 'รถ (แถวที่ 1)' ไม่ครบ
    ทั้งที่กรอกรถมาแล้ว เพราะ spec เรียกช่องว่า truck แต่ตารางเก็บเป็น truck_code + truck_plate
    """
    SPEC = {
        "item_fields": [
            {"key": "truck", "label": "รถ", "required": True},
            {"key": "truck_type", "label": "ประเภทรถ", "required": True},
            {"key": "remark", "label": "รายการที่ทำ", "required": True},
        ],
    }
    ROW = {"truck_code": "6604", "truck_plate": "72-8179", "truck_type": "โม่ใหญ่",
           "remark": "เปลี่ยนยาง 6 เส้น"}

    def _missing(self, row):
        out = V.validate_claim({}, [row], self.SPEC, V.STAGE_APPROVE, {})
        return {m["key"] for m in out["missing"]}

    def test_a_complete_row_passes(self):
        assert self._missing(self.ROW) == set()

    def test_a_row_with_no_truck_number_is_caught(self):
        assert "truck" in self._missing({**self.ROW, "truck_code": ""})

    def test_a_row_with_no_plate_is_caught(self):
        # ทะเบียนเป็นส่วนหนึ่งของคีย์ด่านซ้ำเข้ม ขาดไปแล้วด่านซ้ำอ่อนลง
        assert "truck" in self._missing({**self.ROW, "truck_plate": None})

    def test_the_truck_type_is_read_not_assumed_missing(self):
        assert "truck_type" in self._missing({**self.ROW, "truck_type": None})

    def test_the_remark_is_read_not_assumed_missing(self):
        assert "remark" in self._missing({**self.ROW, "remark": "  "})


class TestRuleConditionsOnFields:
    """REQUIREMENT §0/§3.3/§6.5: บิลร้านแต่โอนเข้าชื่อบุคคล ต้องแนบบัตรประชาชน — ร้านจดทะเบียน/จ่ายเงินสด ไม่ต้อง
    กฎเขียนใน spec เป็นเงื่อนไขเทียบค่าช่องหัวใบ (ใน detail หรือคอลัมน์ของใบ)"""
    SPEC = {"attachments": [{"key": "id_card", "label": "สำเนาบัตรประชาชนผู้รับเงิน", "required": False}],
            "rules": [{"when": "payment_method == TRANSFER && payee_type == PERSON",
                       "require_attachment": "id_card", "at": "BEFORE_APPROVE"}]}

    def _missing(self, detail, attachments=None):
        out = V.validate_claim({"amount": 1200, "detail": detail}, [], self.SPEC, V.STAGE_APPROVE, attachments or {})
        return [m["key"] for m in out["missing"]]

    def test_transfer_to_a_person_needs_the_id_card(self):
        assert self._missing({"payment_method": "TRANSFER", "payee_type": "PERSON"}) == ["id_card"]

    def test_transfer_to_a_registered_shop_does_not(self):
        assert self._missing({"payment_method": "TRANSFER", "payee_type": "REGISTERED_SHOP"}) == []

    def test_cash_to_a_person_does_not(self):
        assert self._missing({"payment_method": "CASH", "payee_type": "PERSON"}) == []

    def test_attached_id_card_satisfies_it(self):
        assert self._missing({"payment_method": "TRANSFER", "payee_type": "PERSON"}, {"id_card": 1}) == []

    def test_condition_can_read_a_claim_column_too(self):
        spec = {"rules": [{"when": "expense_type == TIRE", "require_field": "receipt_no", "at": "BEFORE_APPROVE"}]}
        out = V.validate_claim({"expense_type": "TIRE", "receipt_no": ""}, [], spec, V.STAGE_APPROVE, {})
        assert [m["key"] for m in out["missing"]] == ["receipt_no"]

    def test_unknown_condition_never_blocks(self):
        spec = {"rules": [{"when": "moon_phase == FULL", "require_attachment": "id_card", "at": "BEFORE_APPROVE"}]}
        assert V.validate_claim({}, [], spec, V.STAGE_APPROVE, {})["missing"] == []
