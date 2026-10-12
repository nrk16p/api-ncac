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
    def test_single_amount_not_item_based(self, specs):
        assert specs["TOLL"].get("item_based") is False

    def test_receipt_required_before_approve(self, specs):
        keys = [a["key"] for a in specs["TOLL"]["attachments"] if a.get("required")]
        assert "receipt" in keys

    def test_no_receipt_substitute_print(self, specs):
        assert "RECEIPT_SUBSTITUTE" not in specs["TOLL"].get("prints", [])


class TestTire:
    def test_item_based_with_atms_sku(self, specs):
        assert specs["TIRE"]["item_based"] is True
        assert specs["TIRE"]["atms"]["sku"] == "LB00009"

    def test_hard_duplicate_uses_receipt_and_truck(self, specs):
        assert specs["TIRE"]["duplicate_check"]["hard"]["keys"] == ["ref_no", "truck_code", "truck_plate"]


class TestRepair:
    def test_requires_a_photo_of_the_work(self, specs):
        keys = [a["key"] for a in specs["REPAIR"]["attachments"] if a.get("required")]
        assert "photo" in keys and "receipt" in keys


class TestTravel:
    def test_not_item_based_and_keyed_on_driver(self, specs):
        assert specs["TRAVEL"]["item_based"] is False
        assert specs["TRAVEL"]["duplicate_check"]["soft"]["keys"] == ["event_date", "driver"]


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
