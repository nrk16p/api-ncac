from datetime import date, datetime

import numpy as np
import pytest

from series_build import Reading, build_series_doc, bucket_readings, coverage, path_km, to_number
from series_codec import decode_columns


def R(sec, fuel=None, speed=0.0, engine=1, lat=13.70, lng=100.50):
    return Reading(sec=sec, fuel=fuel, speed=speed, engine=engine, lat=lat, lng=lng)


def test_to_number():
    assert to_number("1.5") == 1.5 and to_number(None) is None
    assert to_number("x") is None and to_number(float("nan")) is None


def test_bucket_median_min_max_per_minute():
    cols = bucket_readings([R(36000, 50.0), R(36020, 52.0), R(36040, None), R(36065, None)], "cpct")
    assert cols["m"].tolist() == [600, 601]
    assert cols["fuel"].tolist() == [5100, -1]
    assert cols["fuel_lo"].tolist() == [5000, -1]
    assert cols["fuel_hi"].tolist() == [5200, -1]


def test_bucket_speed_engine_and_last_position():
    cols = bucket_readings([
        R(100, 10.0, speed=0, engine=0, lat=13.0, lng=100.0),
        R(110, 10.0, speed=42.4, engine=1, lat=13.5, lng=100.5),
    ], "dl")
    assert cols["speed"].tolist() == [42]
    assert cols["engine"].tolist() == [1]
    assert cols["lat"].tolist() == [1350000]
    assert cols["fuel"].tolist() == [100]


def test_bucket_unsorted_input_uses_latest_position():
    cols = bucket_readings([R(110, lat=13.5, lng=100.5), R(100, lat=13.0, lng=100.0)], "dl")
    assert cols["lat"].tolist() == [1350000]


def test_bucket_duplicate_timestamps_are_kept():
    cols = bucket_readings([R(60, 100.0), R(60, 102.0)], "dl")
    assert cols["fuel"].tolist() == [1010]


def test_path_km_skips_missing_and_jumps():
    lat = np.array([1370000, 1371000, 0, 1372000, 1472000])  # 0.01° steps ≈ 1.11 km, then a 111 km jump
    lng = np.array([10050000, 10050000, 0, 10050000, 10050000])
    assert path_km(lat, lng) == pytest.approx(2.22, abs=0.02)


def moving_hour(fuel_at):
    # 60 minutes driving north 0.01° per minute ≈ 66 km, engine on
    return [R(i * 60, fuel_at(i), speed=60, engine=1, lat=13.0 + 0.01 * i, lng=100.5) for i in range(60)]


def test_coverage_status_rules():
    empty = bucket_readings([], "dl")
    assert coverage(empty, 0)["status"] == "no_data"
    assert coverage(empty, 0, offline=True)["status"] == "offline"
    assert coverage(bucket_readings([R(60, None), R(120, None)], "dl"), 2)["status"] == "no_sensor"
    assert coverage(bucket_readings(moving_hour(lambda i: 80.0), "dl"), 60)["status"] == "stuck"
    assert coverage(bucket_readings(moving_hour(lambda i: 80.0 - i * 0.1), "dl"), 60)["status"] == "ok"
    parked_flat = [R(i * 60, 80.0, speed=0, engine=1) for i in range(60)]
    assert coverage(bucket_readings(parked_flat, "dl"), 60)["status"] == "ok"


def test_coverage_numbers():
    cov = coverage(bucket_readings([R(600 * 60, 50.0), R(700 * 60, None)], "dl"), 2)
    assert cov["minutes"] == 2 and cov["points"] == 2
    assert cov["fuel_valid_share"] == 0.5
    assert cov["first"] == "10:00" and cov["last"] == "11:40"
    assert cov["max_gap_min"] == 1439 - 700


def test_build_series_doc_shape():
    now = datetime(2026, 10, 6, 1, 0)
    doc = build_series_doc(plate="สบ.71-8635", truck_code="ME152", day=date(2026, 10, 5), source="besttech",
                           unit="cpct", tank_l=200, tank_from="default",
                           readings=[R(60, 55.0), R(120, 54.5)], now=now)
    assert doc["_id"] == "สบ.71-8635|2026-10-05|besttech"
    assert doc["date"] == datetime(2026, 10, 4, 17, 0)
    assert doc["n"] == 2 and doc["enc"] == 1 and doc["fuel_unit"] == "cpct"
    assert decode_columns(doc["cols"], 2)["fuel"].tolist() == [5500, 5450]
    assert doc["ingested_at"] == now


def test_build_series_doc_without_readings_has_no_cols():
    doc = build_series_doc(plate="สบ.71-8623", truck_code="ME162", day=date(2026, 10, 5), source="besttech",
                           unit="cpct", tank_l=200, tank_from="default", readings=[], offline=True)
    assert doc["n"] == 0 and "cols" not in doc
    assert doc["coverage"]["status"] == "offline"


def test_same_inputs_give_same_id():
    kwargs = dict(plate="สบ.71-8635", truck_code=None, day=date(2026, 10, 5), source="terminus",
                  unit="dl", tank_l=200, tank_from="default", readings=[R(60, 1.0)])
    assert build_series_doc(**kwargs)["_id"] == build_series_doc(**kwargs)["_id"]
