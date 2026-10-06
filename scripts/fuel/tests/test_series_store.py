from pymongo import ReplaceOne

from series_store import (DEFAULT_TANK_L, SERIES, TTL_DAYS, ensure_indexes, recent_plates, tank_for,
                          upsert_series)


class FakeCollection:
    def __init__(self):
        self.indexes, self.writes, self.distinct_calls = [], [], []

    def create_index(self, keys, **kwargs):
        self.indexes.append((keys, kwargs))

    def bulk_write(self, ops, ordered=True):
        self.writes.append((ops, ordered))

    def distinct(self, field, query):
        self.distinct_calls.append((field, query))
        return ["สบ.71-0001", "สบ.71-0001", "สบ.71-0002"]


class FakeDB(dict):
    def __getitem__(self, name):
        return self.setdefault(name, FakeCollection())


def test_ensure_indexes_sets_ttl_on_date():
    db = FakeDB()
    ensure_indexes(db)
    ttl = [kwargs for keys, kwargs in db[SERIES].indexes if keys == [("date", 1)]]
    assert ttl == [{"name": "ttl_date", "expireAfterSeconds": TTL_DAYS * 86400}]


def test_upsert_series_replaces_by_id():
    db = FakeDB()
    assert upsert_series(db, [{"_id": "a"}, {"_id": "b"}]) == 2
    ops, ordered = db[SERIES].writes[0]
    assert ordered is False and len(ops) == 2 and all(isinstance(op, ReplaceOne) for op in ops)


def test_upsert_series_empty_is_noop():
    db = FakeDB()
    assert upsert_series(db, []) == 0 and db[SERIES].writes == []


def test_tank_for_defaults_and_known():
    assert tank_for({}, "x") == (DEFAULT_TANK_L, "default")
    assert tank_for({"x": {"tank_l": 180, "tank_from": "calibrated"}}, "x") == (180.0, "calibrated")


def test_recent_plates_queries_previous_days_only():
    db = FakeDB()
    assert recent_plates(db, "terminus", "2026-10-05", lookback_days=2) == {"สบ.71-0001", "สบ.71-0002"}
    _, query = db[SERIES].distinct_calls[0]
    assert query == {"source": "terminus", "date_key": {"$in": ["2026-10-04", "2026-10-03"]}, "n": {"$gt": 0}}
