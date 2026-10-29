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


class _Cursor(list):
    def sort(self, *a):
        return self

    def batch_size(self, *a):
        return self


class _Col:
    def __init__(self, docs=()):
        self.docs = {d.get("_id", i): dict(d) for i, d in enumerate(docs)}

    def find(self, query=None, projection=None):
        return _Cursor(d for d in self.docs.values() if all(d.get(k) == v for k, v in (query or {}).items()))

    def distinct(self, field, query):
        return [d[field] for d in self.find(query)]

    def bulk_write(self, ops, ordered=True):
        for op in ops:
            self.docs[op._doc["_id"]] = op._doc


def _parked(plate, minute, volts=27.0):
    return {"ทะเบียนพาหนะ": plate, "วันที่": "05/10/2026", "เวลา": f"08:{minute:02d}:00", "Voltage": volts,
            "สถานะ": "จอดรถ", "สถานที่": "แพลนต์", "พิกัด": "14.5,100.5"}


def test_besttech_docs_are_not_overwritten(monkeypatch):
    import pipeline_engineon
    log = _Col([_parked(p, m) for p in ("71-0001", "71-0002") for m in range(6)])
    summary = _Col([{"_id": "71-0002_2026-10-05", "date": "05/10/2026", "version_type": "besttech",
                     "total_engine_on_min": 42.0}])
    cols = {("terminus", "driving_log"): log, ("atms", "plants"): _Col([{"plant_code": "P1", "Latitude": 14.5,
                                                                       "Longitude": 100.5}]),
            ("analytics", "raw_engineon"): _Col(), ("analytics", "summary_engineon"): summary}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __getitem__(self, db):
            return {name: col for (d, name), col in cols.items() if d == db}

    monkeypatch.setattr(pipeline_engineon, "MongoClient", _Client)
    process_engineon_data_optimized("mongodb://x", start_date="05/10/2026", end_date="05/10/2026")
    assert summary.docs["71-0002_2026-10-05"]["total_engine_on_min"] == 42.0       # Besttech kept
    assert summary.docs["71-0001_2026-10-05"]["version_type"] == "v2"              # Terminus written
    assert summary.docs["71-0001_2026-10-05"]["total_engine_on_min"] == pytest.approx(5)
