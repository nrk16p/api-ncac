from datetime import date

from series_besttech import besttech_day_docs
from series_codec import decode_columns

DAY = date(2026, 10, 5)
TRACK = [
    {"vehicle_no": "ME152 (71-8635 สบ.)", "state": "ON_RUN", "gps_time": "2026-10-06 11:18:55"},
    {"vehicle_no": "ME162 (71-8623 สบ.)", "state": "OFFLINE", "gps_time": "2026-06-11 15:06:33"},
    {"vehicle_no": "70-6294 สบ.", "state": "OFF", "gps_time": "2026-10-06 08:00:00"},
]


def pt(t, fuel=55.0, engine="ON", speed=0):
    return {"gps_time": t, "fuel_percentage": fuel, "engine": engine, "speed": speed, "lat": 14.28, "lng": 100.78}


def by_plate(docs):
    return {d["plate"]: d for d in docs}


def test_points_become_one_doc_per_plate():
    windows = [[{"vehicle_no": "ME152 (71-8635 สบ.)", "points": [
        pt("2026-10-05 00:00:16", 55.0), pt("2026-10-05 00:00:46", 54.0), pt("2026-10-05 10:15:00", -1),
    ]}]]
    d = by_plate(besttech_day_docs(DAY, TRACK, windows, {}))["สบ.71-8635"]
    assert d["truck_code"] == "ME152" and d["fuel_unit"] == "cpct" and d["n"] == 2
    cols = decode_columns(d["cols"], d["n"])
    assert cols["m"].tolist() == [0, 615]
    assert cols["fuel"].tolist() == [5450, -1]


def test_silent_vehicles_get_offline_or_no_data_docs():
    docs = by_plate(besttech_day_docs(DAY, TRACK, [[]], {}))
    assert docs["สบ.71-8623"]["coverage"]["status"] == "offline"
    assert docs["สบ.70-6294"]["coverage"]["status"] == "no_data"
    assert docs["สบ.71-8635"]["coverage"]["status"] == "no_data"


def test_points_outside_the_day_are_dropped():
    windows = [[{"vehicle_no": "ME152 (71-8635 สบ.)",
                 "points": [pt("2026-10-04 23:59:59"), pt("2026-10-06 00:00:01")]}]]
    assert by_plate(besttech_day_docs(DAY, TRACK, windows, {}))["สบ.71-8635"]["n"] == 0


def test_box_swap_two_codes_one_plate_merges():
    windows = [[
        {"vehicle_no": "ME152 (71-8635 สบ.)", "points": [pt("2026-10-05 08:00:00", 60.0)]},
        {"vehicle_no": "ME999 (71-8635 สบ.)", "points": [pt("2026-10-05 09:00:00", 59.0)]},
    ]]
    docs = [d for d in besttech_day_docs(DAY, [], windows, {}) if d["plate"] == "สบ.71-8635"]
    assert len(docs) == 1 and docs[0]["n"] == 2 and docs[0]["truck_code"] == "ME152"


def test_empty_hours_still_build_the_day():
    windows = [[] for _ in range(23)] + [[{"vehicle_no": "70-6294 สบ.", "points": [pt("2026-10-05 23:10:00")]}]]
    d = by_plate(besttech_day_docs(DAY, TRACK, windows, {}))["สบ.70-6294"]
    assert d["n"] == 1 and d["coverage"]["max_gap_min"] == 23 * 60 + 10


def test_tank_size_comes_from_tanks():
    windows = [[{"vehicle_no": "ME152 (71-8635 สบ.)", "points": [pt("2026-10-05 08:00:00")]}]]
    tanks = {"สบ.71-8635": {"tank_l": 180.0, "tank_from": "calibrated"}}
    d = by_plate(besttech_day_docs(DAY, TRACK, windows, tanks))["สบ.71-8635"]
    assert (d["tank_l"], d["tank_from"]) == (180.0, "calibrated")


def test_string_numbers_are_accepted():
    windows = [[{"vehicle_no": "ME152 (71-8635 สบ.)", "points": [
        {"gps_time": "2026-10-05 08:00:00", "fuel_percentage": "92.8", "engine": "ON", "speed": "12",
         "lat": "13.7957633", "lng": "100.5571733"}]}]]
    d = by_plate(besttech_day_docs(DAY, [], windows, {}))["สบ.71-8635"]
    cols = decode_columns(d["cols"], d["n"])
    assert cols["fuel"].tolist() == [9280] and cols["speed"].tolist() == [12] and cols["lat"].tolist() == [1379576]
