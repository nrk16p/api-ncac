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
    pattern = petty_master.search_pattern("4285")
    assert coll.last_filter == {"$or": [{"truck_no": {"$regex": pattern, "$options": "i"}},
                                        {"plate": {"$regex": pattern, "$options": "i"}}]}


class TestAnyPartOfCodeOrPlate:
    """ผู้ใช้สั่ง 30/10: ค้นแบบ autocomplete พิมพ์ส่วนไหนของเบอร์รถหรือทะเบียนก็เจอ
    ทะเบียนจริงมีจุด/ขีด ("สบ.71-1325") คนพิมพ์มักไม่ใส่ — ตัวคั่นต้องไม่ทำให้หาไม่เจอ"""

    import re as _re

    def _hit(self, query, text):
        return self._re.search(petty_master.search_pattern(query), text, self._re.I) is not None

    def test_digits_without_separators_find_a_dashed_plate(self):
        assert self._hit("711325", "สบ.71-1325")

    def test_province_and_number_without_the_dot(self):
        assert self._hit("สบ71", "สบ.71-1325")

    def test_any_middle_part_of_the_code(self):
        assert self._hit("109", "TH1093") and self._hit("th10", "TH1093")

    def test_typed_separators_are_optional_too(self):
        assert self._hit("71 1325", "สบ.71-1325") and self._hit("71-13", "สบ.711325")

    def test_regex_characters_are_taken_literally(self):
        assert not self._hit("7.*", "สบ.71-1325")
        assert petty_master.search_pattern("(1") == "\\(" + petty_master.SEPARATORS + "1"

    def test_closest_matches_come_first(self, monkeypatch):
        _patch(monkeypatch, [
            {**TH1093, "truck_no": "TH2109", "plate": "สบ.70-0001"},   # 109 อยู่กลางเบอร์รถ
            {**TH1093, "truck_no": "TH5555", "plate": "สบ.71-1090"},   # 109 อยู่ในทะเบียน
            {**TH1093, "truck_no": "109", "plate": "สบ.72-2222"},      # เบอร์รถตรงเป๊ะ
            {**TH1093, "truck_no": "TH1093", "plate": "สบ.71-1325"},   # เบอร์รถขึ้นต้น/ลงท้ายด้วยเลขที่พิมพ์
        ])
        codes = [t["truck_code"] for t in petty_master.search_trucks("109")]
        assert codes[0] == "109"
        assert codes.index("TH1093") < codes.index("TH5555")
        assert codes.index("TH2109") < codes.index("TH5555")

    def test_match_rank_is_none_when_nothing_matches(self):
        assert petty_master.match_rank("999", "TH1093", "สบ.71-1325") is None


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
