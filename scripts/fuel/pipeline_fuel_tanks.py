"""fuel_tanks — tank size per plate and re-stamp of tank_l / tank_from on gps_series (spec §3.4).

Order: ATMS ความจุถังน้ำมัน → calibrated (Besttech % vs Terminus litres in the same minute, both
parked, CALIB_FROM..CALIB_TO, default 2026-06-01..2026-08-31) → observed (p99.5 of the minute-median
litre readings in the last 30 days, rounded up to 10 L) → 200 L default. Only metadata is updated; columns stay as-is.
"""
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engineon"))
from common import MONGODB_URI, JobLog, log, now_bkk  # noqa: E402
import numpy as np  # noqa: E402
from pymongo import MongoClient, ReplaceOne  # noqa: E402

from plates import normalize_plate  # noqa: E402
from series_codec import decode_column, decode_columns  # noqa: E402
from series_store import SERIES, TANKS  # noqa: E402
from tanks import fit_tank, high_reading, observed_tank, pair_minutes, parse_capacity, resolve_tank  # noqa: E402

OBSERVED_DAYS = 30


def atms_capacities(atms_db) -> dict[str, float]:
    out = {}
    for doc in atms_db["vehiclemaster"].find({}, {"ทะเบียน": 1, "ความจุถังน้ำมัน": 1}):
        plate, capacity = normalize_plate(doc.get("ทะเบียน")), parse_capacity(doc.get("ความจุถังน้ำมัน"))
        if plate and capacity:
            out[plate] = capacity
    return out


def calibration_pairs(db, date_from: str, date_to: str) -> dict[str, list[tuple[float, float]]]:
    pairs: dict[str, list[tuple[float, float]]] = defaultdict(list)
    query = {"source": "besttech", "n": {"$gt": 0}, "date_key": {"$gte": date_from, "$lte": date_to}}
    for bt in db[SERIES].find(query, {"plate": 1, "date_key": 1, "n": 1, "cols": 1}):
        te = db[SERIES].find_one({"_id": f"{bt['plate']}|{bt['date_key']}|terminus", "n": {"$gt": 0}},
                                 {"n": 1, "cols": 1})
        if te:
            pairs[bt["plate"]] += pair_minutes(decode_columns(bt["cols"], bt["n"]),
                                               decode_columns(te["cols"], te["n"]))
    return pairs


def observed_maxima(db, since: str) -> dict[str, float]:
    """Per plate: the p99.5 (tanks.high_reading) of every valid minute-median `fuel` reading since
    `since`, in litres — pooled over the days, so a spike or a stuck-high stretch cannot size the tank."""
    minutes: dict[str, list] = defaultdict(list)
    query = {"fuel_unit": "dl", "n": {"$gt": 0}, "date_key": {"$gte": since}}
    for doc in db[SERIES].find(query, {"plate": 1, "n": 1, "cols.fuel": 1}):
        fuel = decode_column(doc["cols"]["fuel"], "fuel", doc["n"])
        fuel = fuel[fuel >= 0]
        if fuel.size:
            minutes[doc["plate"]].append(fuel)   # int16 deci-litres: ≤ 90 KB per plate for 30 days
    out = {}
    for plate, parts in minutes.items():
        high = high_reading(np.concatenate(parts))
        if high is not None:
            out[plate] = high / 10.0
    return out


def main() -> None:
    date_from = os.getenv("CALIB_FROM", "2026-06-01")
    date_to = os.getenv("CALIB_TO", "2026-08-31")
    job = JobLog("fuel_tanks", "fuel_tanks", {"calib_from": date_from, "calib_to": date_to})
    try:
        client = MongoClient(MONGODB_URI)
        db = client["analytics"]
        atms = atms_capacities(client["atms"])
        pairs = calibration_pairs(db, date_from, date_to)
        observed = observed_maxima(db, (now_bkk().date() - timedelta(days=OBSERVED_DAYS)).isoformat())
        stamp = datetime.now(timezone.utc).replace(tzinfo=None)
        counts: dict[str, int] = defaultdict(int)
        ops = []
        for plate in db[SERIES].distinct("plate"):
            tank = resolve_tank(atms_l=atms.get(plate), fit=fit_tank(pairs.get(plate, [])),
                                observed_l=observed_tank(observed.get(plate)))
            counts[tank["tank_from"]] += 1
            ops.append(ReplaceOne({"_id": plate}, {"_id": plate, **tank, "updated_at": stamp}, upsert=True))
            db[SERIES].update_many(
                {"plate": plate, "$or": [{"tank_l": {"$ne": tank["tank_l"]}},
                                         {"tank_from": {"$ne": tank["tank_from"]}}]},
                {"$set": {"tank_l": tank["tank_l"], "tank_from": tank["tank_from"]}})
        if ops:
            db[TANKS].bulk_write(ops, ordered=False)
        log.info("fuel_tanks: %s", dict(counts))
        job.finish("success", records=len(ops), **{f"from_{k}": v for k, v in counts.items()})
    except Exception as e:
        job.finish("failed", error=str(e))
        raise


if __name__ == "__main__":
    main()
