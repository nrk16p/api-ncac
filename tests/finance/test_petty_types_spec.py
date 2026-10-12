import json
import pathlib

import pytest

from services.finance import petty_validate as V

SQL = pathlib.Path(__file__).resolve().parents[2] / "scripts/migrations/2026-10-13_finance_petty_types.sql"


def _specs() -> dict:
    """อ่าน spec ออกจากไฟล์ migration เพื่อให้เทสต์ชี้ไปที่ของจริงที่จะรันกับฐาน"""
    text = SQL.read_text(encoding="utf-8")
    out = {}
    for code in ("TOLL", "TIRE", "REPAIR", "TRAVEL"):
        body = text.split(f"-- spec:{code}")[1].split("-- /spec")[0].strip()
        out[code] = json.loads(body.strip().strip("'"))
    return out


@pytest.fixture(scope="module")
def specs():
    return _specs()


class TestToll:
    """ผู้ใช้สั่ง 2026-10-12: เหมือนค่าเดินทาง ต่างที่ต้องใช้ใบเสร็จตัวจริง"""

    def test_single_amount_not_item_based(self, specs):
        assert specs["TOLL"].get("item_based") is False

    def test_receipt_required_before_approve(self, specs):
        keys = [a["key"] for a in specs["TOLL"]["attachments"] if a.get("required")]
        assert "receipt" in keys

    def test_the_receipt_label_says_the_original_is_needed(self, specs):
        receipt = next(a for a in specs["TOLL"]["attachments"] if a["key"] == "receipt")
        assert "ตัวจริง" in receipt["label"]

    def test_same_header_shape_as_travel(self, specs):
        toll = {f["key"] for f in specs["TOLL"]["header_fields"]}
        travel = {f["key"] for f in specs["TRAVEL"]["header_fields"]}
        assert {"event_date", "route_from", "route_to", "rate"} <= toll & travel

    def test_no_receipt_substitute_print(self, specs):
        assert "RECEIPT_SUBSTITUTE" not in specs["TOLL"].get("prints", [])


class TestTire:
    def test_item_based_with_atms_sku(self, specs):
        assert specs["TIRE"]["item_based"] is True
        assert specs["TIRE"]["atms"]["sku"] == "LB00009"

    def test_hard_duplicate_uses_receipt_and_truck(self, specs):
        assert specs["TIRE"]["duplicate_check"]["hard"]["keys"] == ["ref_no", "truck_code", "truck_plate"]

    def test_product_group_matches_the_real_dd(self, specs):
        # DD จริง LBDD26090680 ลงกลุ่มสินค้า "ค่าแรง-ระบบยาง" ไม่ใช่ "ค่าแรง" เฉย ๆ
        assert specs["TIRE"]["atms"]["product_group"] == "ค่าแรง-ระบบยาง"

    def test_counts_in_tyres(self, specs):
        qty = next(f for f in specs["TIRE"]["item_fields"] if f["key"] == "qty")
        assert "เส้น" in qty["label"]


class TestRepair:
    """ผู้ใช้สั่ง 2026-10-12: ใช้โครงเดียวกับค่าชั่งน้ำหนัก — รายการ + เลขอ้างอิงบังคับ + ใบรับรองแทนใบเสร็จ"""

    def test_item_based_like_weigh(self, specs):
        assert specs["REPAIR"]["item_based"] is True

    def test_reference_number_required_on_every_row(self, specs):
        ref = next(f for f in specs["REPAIR"]["item_fields"] if f["key"] == "ref_no")
        assert ref["required"] is True

    def test_prints_the_receipt_substitute(self, specs):
        assert "RECEIPT_SUBSTITUTE" in specs["REPAIR"]["prints"]

    def test_photo_is_optional_like_weigh(self, specs):
        photo = next(a for a in specs["REPAIR"]["attachments"] if a["key"] == "photo")
        assert not photo.get("required")


class TestTravel:
    """ของจริงคือค่าแท็กซี่/Grab ของพนักงาน ไม่ใช่เบี้ยเลี้ยง พจส."""

    def test_not_item_based_and_keyed_on_driver(self, specs):
        assert specs["TRAVEL"]["item_based"] is False
        assert specs["TRAVEL"]["duplicate_check"]["soft"]["keys"] == ["event_date", "driver"]

    def test_the_approved_request_form_is_required(self, specs):
        keys = [a["key"] for a in specs["TRAVEL"]["attachments"] if a.get("required")]
        assert "request_form" in keys

    def test_trip_code_is_the_reference_field(self, specs):
        keys = {f["key"] for f in specs["TRAVEL"]["header_fields"]}
        assert "ref_no" in keys and "leg" in keys and "reason" in keys


class TestEveryTypeWorksWithTheSameGate:
    def test_gate_runs_without_error_for_all_four(self, specs):
        claim = {"amount": 1200, "dd_no": None}
        for code, spec in specs.items():
            out = V.validate_claim(claim, [], spec, V.STAGE_APPROVE, {})
            assert isinstance(out["missing"], list), code

    def test_dd_rule_present_on_every_type_over_500(self, specs):
        for code, spec in specs.items():
            rules = [r for r in spec.get("rules", []) if r.get("require_field") == "dd_no"]
            assert rules, f"{code} ยังไม่มีกฎ DD >= 500"

    def test_no_type_mentions_withholding_tax(self, specs):
        # ผู้ใช้สั่ง 2026-10-12: ระบบนี้ไม่ยุ่งเรื่องภาษี
        import json as _json
        for code, spec in specs.items():
            assert "wht" not in _json.dumps(spec, ensure_ascii=False).lower(), code

    def test_every_type_requires_transfer_proof(self, specs):
        for code, spec in specs.items():
            keys = [a["key"] for a in spec["attachments"] if a.get("required")]
            assert "transfer" in keys, code

    def test_every_attachment_entry_has_a_key_and_label(self, specs):
        for code, spec in specs.items():
            for att in spec["attachments"]:
                assert att.get("key") and att.get("label"), code

    def test_every_field_entry_has_a_key_and_label(self, specs):
        for code, spec in specs.items():
            for group in ("header_fields", "item_fields"):
                for field in spec.get(group, []):
                    assert field.get("key") and field.get("label"), f"{code}/{group}"

    def test_specs_pass_the_schema_validator(self, specs):
        from schemas.finance_petty_schema import ExpenseTypeUpdate
        for code, spec in specs.items():
            assert ExpenseTypeUpdate(name_th=code, spec=spec).spec is not None


class TestPrintUnit:
    """หน่วยนับบนใบสำคัญจ่าย — ใบจริงนับ "คัน" (ค่าชั่ง) / "เส้น" (ยาง) ไม่ใช่หน่วย SKU "งาน" ของ ATMS
    (เจอตอนพิมพ์จริง 2026-10-13: ใบชั่งขึ้น "8 งาน")"""

    def test_tire_counts_in_sen(self, specs):
        assert specs["TIRE"]["print"]["unit"] == "เส้น"

    def test_repair_counts_in_khan(self, specs):
        assert specs["REPAIR"]["print"]["unit"] == "คัน"

    def test_weigh_counts_in_khan(self):
        import json, pathlib
        text = (pathlib.Path(__file__).resolve().parents[2] /
                "scripts/migrations/2026-10-12_finance_petty_cash.sql").read_text(encoding="utf-8")
        body = text.split("-- spec:WEIGH")[1].split("-- /spec")[0].strip().strip("'")
        assert json.loads(body)["print"]["unit"] == "คัน"
