from datetime import datetime

from fake_mongo import FakeClient
from pipeline_fuel_events import run_day
from synth import DAY, consumption_day, series, siphon_day

NOW = datetime(2026, 10, 6, 21, 30)


def client_with_trucks():
    client = FakeClient()
    db = client["analytics"]
    sparse = siphon_day()[:10] + [(m, None, 0.0, 0, 13.7, 100.5) for m in range(10, 360)]   # 10 valid minutes of 360
    for plate, points in [("สบ.71-0001", siphon_day()), ("สบ.71-0002", consumption_day()), ("สบ.71-0003", sparse)]:
        doc, _ = series(points, plate=plate)
        db["gps_series"].replace_one({"_id": doc["_id"]}, doc)
    client["analytics"]["engineon_trip_summary"].replace_one(
        {"_id": "71-0001_2026-10-05"}, {"_id": "71-0001_2026-10-05", "Supervisor": "สมชาย ใจดี"})
    return client


def test_run_day_stores_events_stats_and_summary():
    client = client_with_trucks()
    result = run_day(client, DAY, now=NOW)
    db = client["analytics"]
    events = {e["plate"]: e for e in db["fuel_events"].find({})}
    loss = events["สบ.71-0001"]
    assert loss["class"] == "suspected_loss" and loss["status"] == "open" and loss["driver"] == "สมชาย ใจดี"
    assert loss["_id"].startswith("สบ.71-0001|2026-10-05T02:")
    assert events["สบ.71-0002"]["class"] == "consumption"
    assert result["events"] == 2 and result["open"] == 1 and result["scorer"] == "rules-v1"
    summary = db["fuel_daily_summary"].find_one({"_id": "2026-10-05"})
    assert summary["check_first"] == [loss["_id"]] and summary["trucks_analysed"] == 2
    assert summary["by_status"]["sparse"] == 1 and "สบ.71-0003" not in events
    assert summary["sources_missing"] == ["besttech"]
    assert len(db["fuel_day_stats"].find({})) == 2


def test_rerun_keeps_the_reviewers_decision():
    client = client_with_trucks()
    run_day(client, DAY, now=NOW)
    events = client["analytics"]["fuel_events"]
    loss_id = next(e["_id"] for e in events.find({"plate": "สบ.71-0001"}))
    events.update_many({"_id": loss_id}, {"$set": {"status": "decided", "decision": "real_loss", "review_id": "r1"}})
    run_day(client, DAY, now=NOW)
    kept = events.find_one({"_id": loss_id})
    assert kept["status"] == "decided" and kept["decision"] == "real_loss" and kept["review_id"] == "r1"
    assert len(events.find({})) == 2


def test_events_carry_fleet_branch_plant_from_gps_distance():
    client = client_with_trucks()
    client["gps"]["distance_besttech"].replace_one({"_id": "b1"}, {
        "_id": "b1", "vehicle_no": "สบ.71-0001", "date_key": "2026-10-04",
        "fleet": "Asia", "branch": "ลาดกระบัง", "plant": "พะเยา"})
    run_day(client, DAY, now=NOW)
    events = {e["plate"]: e for e in client["analytics"]["fuel_events"].find({})}
    assert (events["สบ.71-0001"]["fleet"], events["สบ.71-0001"]["branch"], events["สบ.71-0001"]["plant"]) == \
        ("Asia", "ลาดกระบัง", "พะเยา")
    assert (events["สบ.71-0002"]["fleet"], events["สบ.71-0002"]["branch"], events["สบ.71-0002"]["plant"]) == \
        (None, None, None)
