"""fuel_series_terminus — terminus.driving_log → analytics.gps_series (spec §3.3, §3.5).

Default day = yesterday (Bangkok). START_DATE / END_DATE (dd/mm/YYYY) or DATES (comma list of
dd/mm/YYYY) choose days; PLATES (comma list, any plate format) limits the trucks.
Reads go through the วันที่-first index in batches of 50 plates with a short pause in between
(TERMINUS_BATCH_SLEEP_S, default 0.5 s) — the cluster is small. น้ำมัน is litres; ระยะทาง(กม.) is
always 0, so distance comes from coordinates (series_build.path_km).
"""
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engineon"))
from common import MONGODB_URI, JobLog, log, yesterday_bkk  # noqa: E402
from pymongo import MongoClient  # noqa: E402

from dates import ddmmyyyy, parse_date_list, parse_days  # noqa: E402
from plates import normalize_plate, terminus_plate  # noqa: E402
from series_build import Reading, build_series_doc, to_number  # noqa: E402
from series_store import ensure_indexes, load_tanks, recent_plates, tank_for, upsert_series  # noqa: E402

SOURCE = "terminus"
UNIT = "dl"
INDEX = "idx_date_plate_status_order_desc"
BATCH = 50
ENGINE_OFF = "ดับเครื่อง"
FIELDS = {"_id": 0, "ทะเบียนพาหนะ": 1, "รหัสพาหนะ": 1, "เวลา": 1, "น้ำมัน": 1,
          "ความเร็ว(กม./ชม.)": 1, "สถานะ": 1, "พิกัด": 1}


def _seconds(value) -> int | None:
    try:
        hours, minutes, seconds = (int(part) for part in str(value).split(":"))
    except ValueError:
        return None
    if not (0 <= hours < 24 and 0 <= minutes < 60 and 0 <= seconds < 60):
        return None
    return hours * 3600 + minutes * 60 + seconds


def _lat_lng(value) -> tuple[float | None, float | None]:
    try:
        lat, lng = (float(part) for part in str(value).split(","))
    except ValueError:
        return None, None
    return lat, lng


def row_reading(row: dict) -> Reading | None:
    sec = _seconds(row.get("เวลา"))
    if sec is None:
        return None
    fuel = to_number(row.get("น้ำมัน"))
    lat, lng = _lat_lng(row.get("พิกัด"))
    return Reading(sec=sec, fuel=fuel if fuel is not None and fuel > 0 else None,
                   speed=to_number(row.get("ความเร็ว(กม./ชม.)")) or 0.0,
                   engine=0 if row.get("สถานะ") == ENGINE_OFF else 1, lat=lat, lng=lng)


def terminus_day_docs(day: date, rows: list[dict], tanks: dict, silent_plates=(),
                      now: datetime | None = None) -> list[dict]:
    readings: dict[str, list[Reading]] = {}
    codes: dict[str, str] = {}
    for row in rows:
        plate = normalize_plate(row.get("ทะเบียนพาหนะ"))
        reading = row_reading(row)
        if not plate or reading is None:
            continue
        readings.setdefault(plate, []).append(reading)
        code = row.get("รหัสพาหนะ")
        if isinstance(code, str) and code.strip() and plate not in codes:
            codes[plate] = code.strip()
    docs = []
    for plate in sorted(set(readings) | set(silent_plates)):
        tank_l, tank_from = tank_for(tanks, plate)
        docs.append(build_series_doc(plate=plate, truck_code=codes.get(plate), day=day, source=SOURCE,
                                     unit=UNIT, tank_l=tank_l, tank_from=tank_from,
                                     readings=readings.get(plate, []), now=now))
    return docs


def ingest_terminus_day(terminus_db, db, day: date, plates: list[str] | None = None,
                        tanks: dict | None = None, batch_pause_s: float | None = None) -> int:
    driving_log = terminus_db["driving_log"]
    key = ddmmyyyy(day)
    pause = float(os.getenv("TERMINUS_BATCH_SLEEP_S", "0.5")) if batch_pause_s is None else batch_pause_s
    if plates:
        raw_plates = sorted({terminus_plate(p) for p in plates})
        expected: set[str] = set()
    else:
        raw_plates = sorted(driving_log.distinct("ทะเบียนพาหนะ", {"วันที่": key}))
        expected = recent_plates(db, SOURCE, day.isoformat())
    tanks = load_tanks(db) if tanks is None else tanks
    seen: set[str] = set()
    written = 0
    for i in range(0, len(raw_plates), BATCH):
        batch = raw_plates[i:i + BATCH]
        rows = list(driving_log.find({"วันที่": key, "ทะเบียนพาหนะ": {"$in": batch}}, FIELDS).hint(INDEX))
        docs = terminus_day_docs(day, rows, tanks)
        seen.update(d["plate"] for d in docs)
        written += upsert_series(db, docs)
        if pause and i + BATCH < len(raw_plates):
            time.sleep(pause)
    written += upsert_series(db, terminus_day_docs(day, [], tanks, silent_plates=expected - seen))
    log.info("terminus %s: %d docs from %d plates", day.isoformat(), written, len(raw_plates))
    return written


def main() -> None:
    yesterday = yesterday_bkk().strftime("%d/%m/%Y")
    dates_env = os.getenv("DATES", "").strip()
    days = parse_date_list(dates_env) if dates_env else parse_days(os.getenv("START_DATE", yesterday),
                                                                   os.getenv("END_DATE", yesterday))
    plates = [p.strip() for p in os.getenv("PLATES", "").split(",") if p.strip()] or None
    job = JobLog("fuel_series_terminus", "fuel_series_terminus",
                 {"first_day": days[0].isoformat(), "last_day": days[-1].isoformat(),
                  "days": len(days), "plates": len(plates or [])})
    try:
        client = MongoClient(MONGODB_URI)
        db = client["analytics"]
        ensure_indexes(db)
        tanks = load_tanks(db)
        written = sum(ingest_terminus_day(client["terminus"], db, day, plates=plates, tanks=tanks)
                      for day in days)
        job.finish("success", records=written)
    except Exception as e:
        job.finish("failed", error=str(e))
        raise


if __name__ == "__main__":
    main()
