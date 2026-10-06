"""fuel_series_besttech — Besttech /history_all → analytics.gps_series (spec §3.3, §3.5).

Default day = yesterday (Bangkok). START_DATE / END_DATE (dd/mm/YYYY) select a range.
A day that already has docs for ≥ 90 % of the /track vehicle list is skipped unless FORCE=1,
so a long backfill can be stopped and restarted. BESTTECH_SPACING_S overrides the 35 s gap.
Local runs read scripts/.env (MONGODB_URI, BESTTECH_API).
"""
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engineon"))
from common import MONGODB_URI, JobLog, log, yesterday_bkk  # noqa: E402
from pymongo import MongoClient  # noqa: E402

from besttech_client import DEFAULT_BASE_URL, BesttechClient  # noqa: E402
from dates import parse_days  # noqa: E402
from plates import split_besttech_vehicle  # noqa: E402
from series_build import Reading, build_series_doc, to_number  # noqa: E402
from series_store import count_series, ensure_indexes, load_tanks, tank_for, upsert_series  # noqa: E402

SOURCE = "besttech"
UNIT = "cpct"
COMPLETE_SHARE = 0.9
TIME_FMT = "%Y-%m-%d %H:%M:%S"


def _parse_time(value) -> datetime | None:
    try:
        return datetime.strptime(str(value or ""), TIME_FMT)
    except ValueError:
        return None


def _reading(point: dict, sec: int) -> Reading:
    fuel = to_number(point.get("fuel_percentage"))
    return Reading(
        sec=sec,
        fuel=fuel if fuel is not None and fuel >= 0 else None,   # -1 = sensor not configured
        speed=to_number(point.get("speed")) or 0.0,
        engine=1 if str(point.get("engine", "")).upper() == "ON" else 0,
        lat=to_number(point.get("lat")),
        lng=to_number(point.get("lng")),
    )


def besttech_day_docs(day: date, track_vehicles: list[dict], windows: list[list[dict]],
                      tanks: dict, now: datetime | None = None) -> list[dict]:
    """One doc per plate for `day`, from the /track list and the day's /history_all windows."""
    readings: dict[str, list[Reading]] = {}
    codes: dict[str, str] = {}
    for vehicles in windows:
        for vehicle in vehicles:
            code, plate = split_besttech_vehicle(vehicle.get("vehicle_no"))
            if not plate:
                continue
            if code and plate not in codes:
                codes[plate] = code   # a box swap reports a second code for the same plate
            for point in vehicle.get("points") or []:
                ts = _parse_time(point.get("gps_time"))
                if ts is None or ts.date() != day:
                    continue
                sec = ts.hour * 3600 + ts.minute * 60 + ts.second
                readings.setdefault(plate, []).append(_reading(point, sec))

    docs = []
    for plate, plate_readings in readings.items():
        tank_l, tank_from = tank_for(tanks, plate)
        docs.append(build_series_doc(plate=plate, truck_code=codes.get(plate), day=day, source=SOURCE,
                                     unit=UNIT, tank_l=tank_l, tank_from=tank_from,
                                     readings=plate_readings, now=now))

    day_start = datetime.combine(day, time.min)
    for vehicle in track_vehicles:
        code, plate = split_besttech_vehicle(vehicle.get("vehicle_no"))
        if not plate or plate in readings:
            continue
        last = _parse_time(vehicle.get("gps_time"))
        tank_l, tank_from = tank_for(tanks, plate)
        docs.append(build_series_doc(plate=plate, truck_code=code, day=day, source=SOURCE, unit=UNIT,
                                     tank_l=tank_l, tank_from=tank_from, readings=[],
                                     offline=last is None or last < day_start, now=now))
    return docs


def fetch_day(client: BesttechClient, day: date) -> list[list[dict]]:
    windows = []
    for hour in range(24):
        start = datetime.combine(day, time(hour, 0, 0))
        windows.append(client.history_all(start, start + timedelta(minutes=59, seconds=59)))
    return windows


def run_days(client: BesttechClient, db, days: list[date], force: bool = False) -> int:
    ensure_indexes(db)
    track = client.track()
    tanks = load_tanks(db)
    written = 0
    for day in days:
        key = day.isoformat()
        if not force and track and count_series(db, key, SOURCE) >= COMPLETE_SHARE * len(track):
            log.info("besttech %s already complete — skipped", key)
            continue
        docs = besttech_day_docs(day, track, fetch_day(client, day), tanks)
        written += upsert_series(db, docs)
        log.info("besttech %s: %d docs", key, len(docs))
    return written


def make_client() -> BesttechClient:
    return BesttechClient(os.getenv("BESTTECH_API", ""),
                          base_url=os.getenv("BESTTECH_BASE_URL", DEFAULT_BASE_URL),
                          spacing_s=float(os.getenv("BESTTECH_SPACING_S", "35")))


def main() -> None:
    yesterday = yesterday_bkk().strftime("%d/%m/%Y")
    start, end = os.getenv("START_DATE", yesterday), os.getenv("END_DATE", yesterday)
    job = JobLog("fuel_series_besttech", "fuel_series_besttech", {"start_date": start, "end_date": end})
    try:
        db = MongoClient(MONGODB_URI)["analytics"]
        written = run_days(make_client(), db, parse_days(start, end), force=os.getenv("FORCE") == "1")
        job.finish("success", records=written)
    except Exception as e:
        job.finish("failed", error=str(e))
        raise


if __name__ == "__main__":
    main()
