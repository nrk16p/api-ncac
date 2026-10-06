"""Mongo access for Part 2 collections (spec §4.5–§4.8): fuel_events, fuel_daily_summary,
fuel_day_stats, fuel_models, fuel_places; plus the driver lookup in engineon_trip_summary and the
fleet / branch / plant lookup in gps.distance_<source>."""
from collections import Counter
from datetime import date, datetime, timedelta

from pymongo import ASCENDING, DESCENDING, ReplaceOne

from plates import terminus_plate

EVENTS = "fuel_events"
SUMMARY = "fuel_daily_summary"
DAY_STATS = "fuel_day_stats"
MODELS = "fuel_models"
PLACES = "fuel_places"
TRIP_SUMMARY = "engineon_trip_summary"
GPS_DB = "gps"
VEHICLE_SOURCES = ("terminus", "besttech")
VEHICLE_FIELDS = ("fleet", "branch", "plant")
VEHICLE_LOOKBACK_DAYS = 30
NO_VALUE = {"", "nan", "none", "null"}


def ensure_event_indexes(db) -> None:
    db[EVENTS].create_index([("date_key", DESCENDING), ("status", ASCENDING), ("score", DESCENDING)],
                            name="date_status_score")
    db[EVENTS].create_index([("plate", ASCENDING), ("start", DESCENDING)], name="plate_start")
    db[DAY_STATS].create_index([("plate", ASCENDING), ("date_key", DESCENDING)], name="plate_date")
    db[DAY_STATS].create_index([("date_key", ASCENDING)], name="date")


def load_existing_events(db, date_key: str) -> list[dict]:
    return list(db[EVENTS].find({"date_key": date_key}))


def write_events(db, upserts: list[dict], stale_ids: list[str], delete_ids: list[str], now: datetime) -> None:
    if upserts:
        db[EVENTS].bulk_write([ReplaceOne({"_id": e["_id"]}, e, upsert=True) for e in upserts], ordered=False)
    if stale_ids:
        db[EVENTS].update_many({"_id": {"$in": stale_ids}}, {"$set": {"stale": True, "updated_at": now}})
    if delete_ids:
        db[EVENTS].delete_many({"_id": {"$in": delete_ids}, "status": {"$ne": "decided"}})


def save_summary(db, summary: dict) -> None:
    db[SUMMARY].replace_one({"_id": summary["_id"]}, summary, upsert=True)


def save_day_stats(db, stats: list[dict]) -> None:
    if stats:
        db[DAY_STATS].bulk_write([ReplaceOne({"_id": s["_id"]}, s, upsert=True) for s in stats], ordered=False)


def load_day_stats(db, date_from: str, date_to: str) -> list[dict]:
    return list(db[DAY_STATS].find({"date_key": {"$gte": date_from, "$lte": date_to}},
                                   {"plate": 1, "date_key": 1, "idle_rates": 1, "km_rates": 1}))


def load_active_model(db) -> dict | None:
    return db[MODELS].find_one({"active": True}, sort=[("created_at", DESCENDING)])


def load_places(db) -> list[dict]:
    return list(db[PLACES].find({}))


def history_counts(db, date_key: str, days: int = 30) -> tuple[Counter, Counter]:
    """Confirmed real losses per plate and per driver in the `days` before `date_key`."""
    day = date.fromisoformat(date_key)
    query = {"decision": "real_loss",
             "date_key": {"$gte": (day - timedelta(days=days)).isoformat(), "$lt": date_key}}
    plates, drivers = Counter(), Counter()
    for e in db[EVENTS].find(query, {"plate": 1, "driver": 1}):
        plates[e["plate"]] += 1
        if e.get("driver"):
            drivers[e["driver"]] += 1
    return plates, drivers


def clean_text(value) -> str | None:
    text = str(value).strip() if value is not None else ""
    return None if text.lower() in NO_VALUE else text


def drivers_for(db, plates: list[str], date_key: str) -> dict[str, str]:
    """Driver per plate for the day from engineon_trip_summary (_id "<raw plate>_<YYYY-MM-DD>",
    driver in Supervisor — the same rule the engine-on report uses)."""
    ids = {f"{terminus_plate(p)}_{date_key}": p for p in set(plates)}
    out = {}
    for row in db[TRIP_SUMMARY].find({"_id": {"$in": list(ids)}}, {"Supervisor": 1}):
        driver = clean_text(row.get("Supervisor"))
        if driver:
            out[ids[row["_id"]]] = driver
    return out


def vehicles_for(gps_db, plates: list[str], date_key: str, days: int = VEHICLE_LOOKBACK_DAYS) -> dict[str, dict]:
    """fleet / branch / plant per plate from gps.distance_<source> — the vehicle master the GPS distance
    ETL stamps on every (vehicle_no, date_key) doc, read through its {date_key, vehicle_no} index.
    Each field takes its latest non-empty value in the `days` up to `date_key`, over both fuel sources:
    one vendor's distance ETL can lag (distance_terminus stopped at 2026-09-13) or leave a field null."""
    if not plates:
        return {}
    day = date.fromisoformat(date_key)
    query = {"date_key": {"$gte": (day - timedelta(days=days)).isoformat(), "$lte": date_key},
             "vehicle_no": {"$in": sorted(set(plates))}}
    projection = {"_id": 0, "vehicle_no": 1, "date_key": 1, **{f: 1 for f in VEHICLE_FIELDS}}
    rows = [row for source in VEHICLE_SOURCES for row in gps_db[f"distance_{source}"].find(query, projection)]
    out: dict[str, dict] = {}
    for row in sorted(rows, key=lambda r: r.get("date_key") or ""):
        vehicle = out.setdefault(row["vehicle_no"], dict.fromkeys(VEHICLE_FIELDS))
        for field in VEHICLE_FIELDS:
            value = clean_text(row.get(field))
            if value:
                vehicle[field] = value
    return out
