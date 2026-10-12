"""ค้นรถจาก atms.truck_master_monthly

ชื่อฟิลด์จริง (ดูจาก Mongo 2026-10-13): truck_no เช่น "TH1093" · plate เช่น "สบ.71-1325" ·
customer เช่น "Scco S" / "Cpac L" (L = โม่ใหญ่, S = โม่เล็ก) · month_year เป็น "M-YY" ปี พ.ศ. สองหลัก
รถหนึ่งคันมีหลายแถว (เดือนละแถว) — ต้องยุบเหลือแถวล่าสุด
"""
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


TH1093 = {"truck_no": "TH1093", "plate": "สบ.71-1325", "customer": "Scco S", "month_year": "9-68"}


def test_finds_by_truck_number(monkeypatch):
    _patch(monkeypatch, [TH1093])
    found = petty_master.search_trucks("1093")
    assert found == [{"truck_code": "TH1093", "truck_plate": "สบ.71-1325", "truck_type": "โม่เล็ก"}]


def test_large_mixer_from_customer_suffix_l(monkeypatch):
    _patch(monkeypatch, [{**TH1093, "truck_no": "TH0161", "customer": "Cpac L"}])
    assert petty_master.search_trucks("0161")[0]["truck_type"] == "โม่ใหญ่"


def test_unknown_customer_leaves_type_blank(monkeypatch):
    _patch(monkeypatch, [{**TH1093, "customer": "Trailer"}])
    assert petty_master.search_trucks("1093")[0]["truck_type"] == ""


def test_query_searches_both_real_fields(monkeypatch):
    coll = _patch(monkeypatch, [])
    petty_master.search_trucks("4285")
    assert coll.last_filter == {"$or": [{"truck_no": {"$regex": "4285", "$options": "i"}},
                                        {"plate": {"$regex": "4285", "$options": "i"}}]}


def test_projection_uses_the_real_field_names(monkeypatch):
    coll = _patch(monkeypatch, [])
    petty_master.search_trucks("1093")
    assert set(coll.last_projection) >= {"truck_no", "plate", "customer", "month_year"}


def test_one_row_per_truck_and_the_newest_month_wins(monkeypatch):
    # เดือน "12-67" ต้องแพ้ "1-68" แม้เรียงตัวอักษรจะกลับกัน
    _patch(monkeypatch, [{**TH1093, "month_year": "12-67", "plate": "เก่า"},
                         {**TH1093, "month_year": "1-68", "plate": "ใหม่"},
                         {**TH1093, "month_year": "9-67", "plate": "เก่ากว่า"}])
    found = petty_master.search_trucks("1093")
    assert len(found) == 1 and found[0]["truck_plate"] == "ใหม่"


def test_short_query_does_not_hit_mongo(monkeypatch):
    coll = _patch(monkeypatch, [TH1093])
    assert petty_master.search_trucks("1") == [] and coll.last_filter is None


def test_mongo_failure_returns_empty(monkeypatch):
    def boom(): raise RuntimeError("down")
    monkeypatch.setattr(petty_master, "_collection", boom)
    assert petty_master.search_trucks("1093") == []


def test_truck_by_code_matches_exactly(monkeypatch):
    _patch(monkeypatch, [TH1093, {**TH1093, "truck_no": "TH10930"}])
    assert petty_master.truck_by_code("TH1093")["truck_plate"] == "สบ.71-1325"
