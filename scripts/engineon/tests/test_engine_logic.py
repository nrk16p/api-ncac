import pytest

from pipeline_engineon import _classify_engine_state, logic_fields, process_engineon_data_optimized, target_collection

NAN = float("nan")


def test_current_logic_is_unchanged():
    assert _classify_engine_state(26.0, "จอดรถ") == "Parking - Engine On"
    assert _classify_engine_state(24.0, "จอดรถ", "v1", "current") == "Parking - Engine Off"
    assert _classify_engine_state(NAN, "จอดรถ", "v1") == "Unknown"
    assert _classify_engine_state(30.0, "รถวิ่ง", "v2", "v2") == "Other"


def test_v2_logic_counts_every_parked_v1_reading():
    assert _classify_engine_state(NAN, "จอดรถ", "v1", "v2") == "Parking - Engine On"
    assert _classify_engine_state(10.0, "จอดรถ", "v1", "v2") == "Parking - Engine On"
    assert _classify_engine_state(10.0, "จอดรถ", "v2", "v2") == "Parking - Engine Off"
    assert _classify_engine_state(26.0, "จอดรถ", "v2", "v2") == "Parking - Engine On"


def test_logic_fields_only_on_v2_runs():
    assert logic_fields("current", "v1") == {}
    assert logic_fields("v2", "v2") == {"engine_logic": "v2", "confirmed_by_voltage": True}
    assert logic_fields("v2", "v1") == {"engine_logic": "v2", "confirmed_by_voltage": False}


def test_target_collection_only_allows_scratch_variants():
    assert target_collection({}, "X", "raw_engineon") == "raw_engineon"
    assert target_collection({"X": "raw_engineon_smoke"}, "X", "raw_engineon") == "raw_engineon_smoke"
    with pytest.raises(ValueError):
        target_collection({"X": "overspeed"}, "X", "raw_engineon")


def test_unknown_logic_fails_before_touching_mongo():
    with pytest.raises(ValueError, match="ENGINE_LOGIC"):
        process_engineon_data_optimized("mongodb://unused", engine_logic="v3")
