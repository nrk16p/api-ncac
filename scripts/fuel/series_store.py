"""Mongo access for analytics.gps_series and analytics.fuel_tanks (spec §3.1, §3.4)."""
from datetime import date, timedelta

from pymongo import ASCENDING, DESCENDING, ReplaceOne

SERIES = "gps_series"
TANKS = "fuel_tanks"
TTL_DAYS = 400
DEFAULT_TANK_L = 200.0


def ensure_indexes(db) -> None:
    col = db[SERIES]
    col.create_index([("date_key", ASCENDING), ("source", ASCENDING)], name="date_source")
    col.create_index([("plate", ASCENDING), ("date_key", DESCENDING)], name="plate_date")
    col.create_index([("date", ASCENDING)], name="ttl_date", expireAfterSeconds=TTL_DAYS * 86400)


def upsert_series(db, docs: list[dict]) -> int:
    if not docs:
        return 0
    db[SERIES].bulk_write([ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in docs], ordered=False)
    return len(docs)


def load_tanks(db) -> dict[str, dict]:
    return {d["_id"]: d for d in db[TANKS].find({}, {"tank_l": 1, "tank_from": 1})}


def tank_for(tanks: dict[str, dict], plate: str) -> tuple[float, str]:
    tank = tanks.get(plate) or {}
    if tank.get("tank_l"):
        return float(tank["tank_l"]), tank.get("tank_from", "default")
    return DEFAULT_TANK_L, "default"


def count_series(db, date_key: str, source: str) -> int:
    return db[SERIES].count_documents({"date_key": date_key, "source": source})


def recent_plates(db, source: str, date_key: str, lookback_days: int = 7) -> set[str]:
    """Plates that had readings for `source` in the `lookback_days` before `date_key`."""
    day = date.fromisoformat(date_key)
    keys = [(day - timedelta(days=i)).isoformat() for i in range(1, lookback_days + 1)]
    return set(db[SERIES].distinct("plate", {"source": source, "date_key": {"$in": keys}, "n": {"$gt": 0}}))
