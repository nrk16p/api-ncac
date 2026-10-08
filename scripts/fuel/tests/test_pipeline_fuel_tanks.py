from fake_mongo import FakeClient
from pipeline_fuel_tanks import observed_maxima
from series_build import Reading, build_series_doc
from synth import DAY


def test_observed_uses_p995_of_minute_medians_not_fuel_hi():
    levels = [150.0] * 300 + [199.0] * 100
    readings = [Reading(sec=m * 60, fuel=v, speed=0.0, engine=0, lat=13.7, lng=100.5) for m, v in enumerate(levels)]
    readings += [Reading(sec=50 * 60 + 20, fuel=420.0, speed=0.0, engine=0, lat=13.7, lng=100.5),   # one-reading spike:
                 Reading(sec=50 * 60 + 40, fuel=150.0, speed=0.0, engine=0, lat=13.7, lng=100.5)]   # fuel_hi 420, median 150
    doc = build_series_doc(plate="สบ.71-0001", truck_code=None, day=DAY, source="terminus", unit="dl",
                           tank_l=200.0, tank_from="default", readings=readings)
    db = FakeClient()["analytics"]
    db["gps_series"].replace_one({"_id": doc["_id"]}, doc)
    assert observed_maxima(db, "2026-10-01") == {"สบ.71-0001": 199.0}
