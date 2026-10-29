from datetime import date, datetime

import pytest

from besttech_derived import (clean_points, engine_on_minutes, make_deriver, overspeed_rows, parked_engine_on,
                              points_by_plate, write_derived)
from fake_mongo import FakeClient

DAY = date(2026, 10, 5)
PLANT = (14.0, 100.0)
SITE = (14.1, 100.1)          # ~15 km from the plant


def pt(t, state="ON_STOP", speed=0, lat=PLANT[0], lng=PLANT[1], engine=None):
    p = {"gps_time": f"2026-10-05 {t}", "speed": speed, "lat": lat, "lng": lng}
    if state is not None:
        p["state"] = state
    if engine is not None:
        p["engine"] = engine
    return p


def minutes(start_h, start_m, count, step_s=60, **kw):
    out = []
    for i in range(count):
        s = start_h * 3600 + start_m * 60 + i * step_s
        out.append(pt(f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}", **kw))
    return out


def test_parked_engine_on_uses_state_or_engine_and_speed():
    assert parked_engine_on({"state": "ON_STOP"})
    assert not parked_engine_on({"state": "ON_RUN", "engine": "ON", "speed": 0})
    assert parked_engine_on({"engine": "ON", "speed": 0})
    assert not parked_engine_on({"engine": "ON", "speed": 12})
    assert not parked_engine_on({"engine": "OFF", "speed": 0})


def test_engine_on_counts_consecutive_pairs_and_splits_plant_time():
    pts = minutes(8, 0, 31) + minutes(10, 0, 11, lat=SITE[0], lng=SITE[1])
    r = engine_on_minutes(clean_points(pts, DAY), [PLANT])
    assert r["plant_min"] == pytest.approx(30)
    assert r["not_plant_min"] == pytest.approx(10)
    assert r["events"] == 2


def test_gaps_over_five_minutes_and_engine_off_do_not_count():
    pts = [pt("08:00:00"), pt("08:06:00"), pt("08:07:00", state="OFF"), pt("08:08:00"), pt("08:09:00")]
    r = engine_on_minutes(clean_points(pts, DAY), [PLANT])
    assert r["plant_min"] == pytest.approx(1)


def test_points_without_position_or_off_day_are_dropped():
    pts = [pt("08:00:00", lat=0, lng=0), {"gps_time": "2026-10-04 23:59:00", "lat": 14, "lng": 100},
           {"gps_time": "bad"}, pt("08:01:00")]
    assert len(clean_points(pts, DAY)) == 1


def test_overspeed_uses_the_terminus_segment_rules():
    # 10 readings 30 s apart at 80 km/h -> one >70 segment (4.5 min, 10 records)
    pts = minutes(9, 0, 10, step_s=30, state="ON_RUN", speed=80)
    for i, p in enumerate(pts):
        p["lat"] = 14.0 + i * 0.006           # ~0.67 km per step
    rows = overspeed_rows("71-0001", clean_points(pts, DAY))
    assert len(rows) == 1
    seg = rows[0]
    assert seg["vehicle"] == "71-0001" and seg["speed_group"] == ">70" and seg["source"] == "besttech"
    assert seg["records"] == 10 and seg["duration_minutes"] == pytest.approx(4.5)
    assert seg["sum_distance_km"] == pytest.approx(6.0, rel=0.02)
    assert seg["start_datetime"] == datetime(2026, 10, 5, 9, 0)


def test_short_bursts_are_not_overspeed():
    pts = minutes(9, 0, 3, step_s=30, state="ON_RUN", speed=90)
    assert overspeed_rows("71-0001", clean_points(pts, DAY)) == []


def test_box_swap_codes_merge_into_one_plate():
    windows = [[{"vehicle_no": "ME152 (71-8635 สบ.)", "points": [pt("08:00:00")]}],
               [{"vehicle_no": "ME999 (71-8635 สบ.)", "points": [pt("08:01:00")]}]]
    assert list(points_by_plate(windows)) == ["71-8635"]
    assert len(points_by_plate(windows)["71-8635"]) == 2


def test_write_replaces_terminus_docs_and_rows_for_trucks_with_points():
    mongo = FakeClient()
    eng, ovs = mongo["analytics"]["summary_engineon"], mongo["analytics"]["overspeed"]
    eng.docs["71-0001_2026-10-05"] = {"_id": "71-0001_2026-10-05", "version_type": "v2", "total_engine_on_min": 99}
    eng.docs["71-0002_2026-10-05"] = {"_id": "71-0002_2026-10-05", "version_type": "v2"}   # no Besttech -> kept
    eng.docs["71-0003_2026-10-05"] = {"_id": "71-0003_2026-10-05", "version_type": "v1"}   # points, 0 min -> gone
    ovs.docs["old"] = {"_id": "old", "vehicle": "71-0001", "start_datetime": datetime(2026, 10, 5, 7)}
    ovs.docs["keep"] = {"_id": "keep", "vehicle": "71-0002", "start_datetime": datetime(2026, 10, 5, 7)}
    windows = [[{"vehicle_no": "ME1 (71-0001 สบ.)", "points": minutes(8, 0, 11)},
                {"vehicle_no": "ME3 (71-0003 สบ.)", "points": minutes(8, 0, 5, state="ON_RUN", speed=30)}]]
    stats = write_derived(DAY, windows, [PLANT], eng, ovs, now=datetime(2026, 10, 6))
    assert stats == {"plates": 2, "engineon_docs": 1, "engineon_replaced": 2, "overspeed_segments": 0}
    doc = eng.docs["71-0001_2026-10-05"]
    assert doc["version_type"] == "besttech" and doc["total_engine_on_min"] == pytest.approx(10)
    assert doc["date"] == "05/10/2026" and doc["ทะเบียนพาหนะ"] == "71-0001"
    assert "71-0002_2026-10-05" in eng.docs and "71-0003_2026-10-05" not in eng.docs
    assert set(ovs.docs) == {"keep"}


def test_deriver_never_raises():
    class Log:
        def __init__(self):
            self.warnings = []

        def info(self, *a):
            pass

        def warning(self, *a):
            self.warnings.append(a)

    log = Log()
    derive = make_deriver(FakeClient(), log)          # atms.plants is empty -> error is reported, not raised
    assert "error" in derive(DAY, [[{"vehicle_no": "ME1 (71-0001 สบ.)", "points": minutes(8, 0, 3)}]])
    assert log.warnings
