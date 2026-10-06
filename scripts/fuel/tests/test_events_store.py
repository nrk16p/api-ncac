from datetime import datetime

from events_store import (EVENTS, REVIEWS, TRIP_SUMMARY, drivers_for, ensure_event_indexes, history_counts,
                          vehicles_for, write_events)


class FakeCollection:
    def __init__(self, rows=()):
        self.rows, self.calls = list(rows), []

    def find(self, query, projection=None):
        self.calls.append(("find", query))
        return list(self.rows)

    def bulk_write(self, ops, ordered=True):
        self.calls.append(("bulk_write", len(ops)))

    def update_many(self, query, update):
        self.calls.append(("update_many", query, update))

    def delete_many(self, query):
        self.calls.append(("delete_many", query))

    def create_index(self, keys, **kwargs):
        self.calls.append(("create_index", keys))


class FakeDB(dict):
    def __getitem__(self, name):
        return self.setdefault(name, FakeCollection())


def test_drivers_from_trip_summary():
    db = FakeDB()
    db[TRIP_SUMMARY] = FakeCollection([{"_id": "71-0429_2026-10-01", "Supervisor": "ฉกาจ วัตวะนะแดง"},
                                       {"_id": "71-0001_2026-10-01", "Supervisor": "nan"}])
    out = drivers_for(db, ["สบ.71-0429", "สบ.71-0001", "สบ.71-0429"], "2026-10-01")
    assert out == {"สบ.71-0429": "ฉกาจ วัตวะนะแดง"}
    _, query = db[TRIP_SUMMARY].calls[0]
    assert sorted(query["_id"]["$in"]) == ["71-0001_2026-10-01", "71-0429_2026-10-01"]


def test_history_counts_previous_30_days():
    db = FakeDB()
    db[EVENTS] = FakeCollection([{"plate": "A", "driver": "x"}, {"plate": "A", "driver": None}, {"plate": "B", "driver": "x"}])
    plates, drivers = history_counts(db, "2026-10-05")
    assert plates == {"A": 2, "B": 1} and drivers == {"x": 2}
    _, query = db[EVENTS].calls[0]
    assert query == {"decision": "real_loss", "date_key": {"$gte": "2026-09-05", "$lt": "2026-10-05"}}


def test_write_events_never_deletes_decided():
    db = FakeDB()
    write_events(db, [{"_id": "a"}], ["s"], ["d"], datetime(2026, 10, 6))
    calls = db[EVENTS].calls
    assert calls[0] == ("bulk_write", 1)
    assert calls[1][0] == "update_many" and calls[1][1] == {"_id": {"$in": ["s"]}}
    assert calls[2] == ("delete_many", {"_id": {"$in": ["d"]}, "status": {"$ne": "decided"}})


def test_vehicles_from_gps_distance_latest_non_empty_value():
    gps = FakeDB()
    gps["distance_terminus"] = FakeCollection([
        {"vehicle_no": "สบ.71-0001", "date_key": "2026-09-13", "fleet": "TDM", "branch": "สระบุรี", "plant": "แก่งคอย"},
        {"vehicle_no": "สบ.71-0002", "date_key": "2026-09-13", "fleet": "nan", "branch": None, "plant": ""}])
    gps["distance_besttech"] = FakeCollection([
        {"vehicle_no": "สบ.71-0001", "date_key": "2026-10-05", "fleet": "Asia", "branch": None, "plant": "พะเยา"}])
    out = vehicles_for(gps, ["สบ.71-0002", "สบ.71-0001", "สบ.71-0001"], "2026-10-05")
    assert out["สบ.71-0001"] == {"fleet": "Asia", "branch": "สระบุรี", "plant": "พะเยา"}
    assert out["สบ.71-0002"] == {"fleet": None, "branch": None, "plant": None}
    _, query = gps["distance_terminus"].calls[0]
    assert query == {"date_key": {"$gte": "2026-09-05", "$lte": "2026-10-05"},
                     "vehicle_no": {"$in": ["สบ.71-0001", "สบ.71-0002"]}}
    assert gps["distance_besttech"].calls[0] == ("find", query)
    assert vehicles_for(FakeDB(), [], "2026-10-05") == {}


def test_reviews_get_an_event_id_index():
    db = FakeDB()
    ensure_event_indexes(db)
    assert REVIEWS == "fuel_drop_reviews" and ("create_index", [("event_id", 1)]) in db[REVIEWS].calls
