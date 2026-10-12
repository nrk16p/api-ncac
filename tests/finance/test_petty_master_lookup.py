import pytest

from services.finance import petty_master


class _FakeCursor(list):
    def limit(self, _n): return self
    def max_time_ms(self, _n): return self


class _FakeColl:
    def __init__(self, rows): self.rows, self.last_filter, self.last_projection = rows, None, None
    def find(self, flt, proj=None):
        self.last_filter, self.last_projection = flt, proj
        return _FakeCursor(self.rows)


def _patch(monkeypatch, rows):
    coll = _FakeColl(rows)
    monkeypatch.setattr(petty_master, "_collection", lambda: coll)
    return coll


def test_finds_by_internal_code(monkeypatch):
    _patch(monkeypatch, [{"เลขรถ": "161", "ทะเบียน": "71-4285", "ประเภทรถ": "โม่ใหญ่"}])
    found = petty_master.search_trucks("161")
    assert found[0]["truck_plate"] == "71-4285" and found[0]["truck_type"] == "โม่ใหญ่"


def test_finds_by_plate(monkeypatch):
    coll = _patch(monkeypatch, [{"เลขรถ": "161", "ทะเบียน": "71-4285"}])
    petty_master.search_trucks("4285")
    assert "$or" in coll.last_filter


def test_query_always_has_a_filter(monkeypatch):
    coll = _patch(monkeypatch, [])
    petty_master.search_trucks("161")
    assert "$or" in coll.last_filter  # ห้ามยิง find({}) กับ cluster นี้


def test_projection_asks_for_three_fields_only(monkeypatch):
    coll = _patch(monkeypatch, [])
    petty_master.search_trucks("161")
    assert set(coll.last_projection) >= {"เลขรถ", "ทะเบียน", "ประเภทรถ"}


def test_short_query_does_not_hit_mongo(monkeypatch):
    def _boom():
        raise AssertionError("ไม่ควรเรียก Mongo")
    monkeypatch.setattr(petty_master, "_collection", _boom)
    assert petty_master.search_trucks("1") == []
    assert petty_master.search_trucks("") == []


def test_duplicate_months_collapse_to_one_truck(monkeypatch):
    _patch(monkeypatch, [{"เลขรถ": "161", "ทะเบียน": "71-4285"},
                         {"เลขรถ": "161", "ทะเบียน": "71-4285"}])
    assert len(petty_master.search_trucks("161")) == 1


def test_rows_without_a_code_are_skipped(monkeypatch):
    _patch(monkeypatch, [{"ทะเบียน": "71-4285"}, {"เลขรถ": "161", "ทะเบียน": "71-4285"}])
    assert [t["truck_code"] for t in petty_master.search_trucks("161")] == ["161"]


def test_mongo_failure_is_not_raised_to_the_caller(monkeypatch):
    class _Boom:
        def find(self, *_a, **_k):
            raise TimeoutError("server selection timeout")
    monkeypatch.setattr(petty_master, "_collection", lambda: _Boom())
    assert petty_master.search_trucks("161") == []


def test_truck_by_code_matches_exactly(monkeypatch):
    _patch(monkeypatch, [{"เลขรถ": "1610", "ทะเบียน": "71-0000"},
                         {"เลขรถ": "161", "ทะเบียน": "71-4285"}])
    assert petty_master.truck_by_code("161")["truck_plate"] == "71-4285"


def test_truck_by_code_returns_none_when_absent(monkeypatch):
    _patch(monkeypatch, [{"เลขรถ": "999", "ทะเบียน": "71-9999"}])
    assert petty_master.truck_by_code("161") is None
