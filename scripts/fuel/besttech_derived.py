"""Engine-on and overspeed from the raw Besttech points the 01:30 pull already fetched.

Same rules as the Terminus jobs, so /engineon and /overspeed read Besttech trucks the same way:
  engine-on (scripts/engineon/pipeline_engineon.py)
    parked with the engine on  -> state "ON_STOP" (or engine ON and speed 0 when state is missing)
    consecutive engine-on pair, 0 < gap <= 5 min; a move > 200 m starts a new event
    an event within 200 m of an atms.plants point counts as plant time
    (Terminus also needs the same place name; Besttech has none, so only the 200 m split applies)
  overspeed (scripts/overspeed/overspeed_segments.py, reused as is)
    > 70 and 60–70 km/h segments, split on gaps > 2 min, kept when > 2 min and >= 5 readings;
    the per-reading distance Terminus reports is the haversine step from the previous point here.

A truck/day with Besttech points belongs to Besttech: its Terminus engine-on doc and overspeed rows
are replaced (dual-box trucks — decided 2026-10-21). The Terminus jobs skip what is marked besttech.
"""
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from pymongo import ReplaceOne

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "overspeed"))
from overspeed_segments import DIST, PLATE, SPEED, plate_segments  # noqa: E402

from plates import split_besttech_vehicle, terminus_plate  # noqa: E402

SOURCE = "besttech"
MAX_DIST_M = 200.0
MAX_GAP_MIN = 5.0
MAX_STEP_KM = 5.0          # larger jumps between two points are GPS glitches, not distance driven
TIME_FMT = "%Y-%m-%d %H:%M:%S"


def haversine_m(lat1, lng1, lat2, lng2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000.0 * math.asin(math.sqrt(min(1.0, a)))


def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parked_engine_on(point: dict) -> bool:
    state = point.get("state")
    if state:
        return state == "ON_STOP"
    return str(point.get("engine", "")).upper() == "ON" and (_num(point.get("speed")) or 0.0) == 0.0


def clean_points(points: list[dict], day: date) -> list[tuple[datetime, float, float, float, bool]]:
    """(time, lat, lng, speed, parked-engine-on) for the day's points with a time and a position, by time."""
    rows = []
    for p in points or []:
        try:
            t = datetime.strptime(str(p.get("gps_time")), TIME_FMT)
        except ValueError:
            continue
        lat, lng = _num(p.get("lat")), _num(p.get("lng"))
        if t.date() != day or lat is None or lng is None or (lat == 0 and lng == 0):
            continue
        rows.append((t, lat, lng, _num(p.get("speed")) or 0.0, parked_engine_on(p)))
    rows.sort(key=lambda r: r[0])
    return rows


def engine_on_minutes(rows, plants: list[tuple[float, float]]) -> dict:
    """{plant_min, not_plant_min, events} — pipeline_engineon.process_plate on Besttech points."""
    valid = []   # (gap minutes, lat, lng) of each consecutive engine-on pair
    for prev, cur in zip(rows, rows[1:]):
        gap = (cur[0] - prev[0]).total_seconds() / 60.0
        if prev[4] and cur[4] and 0 < gap <= MAX_GAP_MIN:
            valid.append((gap, cur[1], cur[2]))
    events = []
    for i, (gap, lat, lng) in enumerate(valid):
        if i == 0 or haversine_m(valid[i - 1][1], valid[i - 1][2], lat, lng) > MAX_DIST_M:
            events.append([0.0, 0.0, 0.0, 0])
        e = events[-1]
        e[0] += gap
        e[1] += lat
        e[2] += lng
        e[3] += 1
    plant = not_plant = 0.0
    for minutes, sum_lat, sum_lng, n in events:
        lat, lng = sum_lat / n, sum_lng / n
        near = min((haversine_m(lat, lng, a, b) for a, b in plants), default=math.inf)
        if near <= MAX_DIST_M:
            plant += minutes
        else:
            not_plant += minutes
    return {"plant_min": plant, "not_plant_min": not_plant, "events": len(events)}


def engine_on_doc(plate: str, day: date, result: dict, points: int, now: datetime) -> dict:
    _id = f"{plate}_{day.isoformat()}"
    return {
        "_id": _id, "ทะเบียนพาหนะ": plate, "date": day.strftime("%d/%m/%Y"),
        "total_engine_on_min": result["plant_min"], "total_engine_on_hr": result["plant_min"] / 60.0,
        "total_engine_on_min_not_plant": result["not_plant_min"],
        "total_engine_on_hr_not_plant": result["not_plant_min"] / 60.0,
        "version_type": SOURCE, "gps_source": SOURCE, "points": points, "events": result["events"],
        "computed_at": now,
    }


def overspeed_frame(plate: str, rows) -> pd.DataFrame:
    """Besttech rows in the frame shape overspeed_segments expects (plate, datetime, speed, step km)."""
    out, prev = [], None
    for t, lat, lng, speed, _ in rows:
        step = 0.0
        if prev is not None:
            step = haversine_m(prev[1], prev[2], lat, lng) / 1000.0
            if step > MAX_STEP_KM:
                step = 0.0
        out.append({PLATE: plate, "datetime": t, SPEED: speed, DIST: step})
        prev = (t, lat, lng)
    return pd.DataFrame(out, columns=[PLATE, "datetime", SPEED, DIST])


def overspeed_rows(plate: str, rows) -> list[dict]:
    if not rows:
        return []
    return [{**seg, "source": SOURCE} for seg in plate_segments(overspeed_frame(plate, rows))]


def points_by_plate(windows: list[list[dict]]) -> dict[str, list[dict]]:
    """{'71-8635': points} from fetch_day windows; a box swap's two codes merge into one plate."""
    out: dict[str, list[dict]] = {}
    for vehicles in windows:
        for vehicle in vehicles:
            _, plate = split_besttech_vehicle(vehicle.get("vehicle_no"))
            if plate:
                out.setdefault(terminus_plate(plate), []).extend(vehicle.get("points") or [])
    return out


def write_derived(day: date, windows: list[list[dict]], plants, engine_col, overspeed_col,
                  now: datetime | None = None) -> dict:
    """Engine-on summary docs + overspeed rows for every plate with Besttech points on `day`."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    ops, ids, plates, segments = [], [], [], []
    for plate, points in sorted(points_by_plate(windows).items()):
        rows = clean_points(points, day)
        if not rows:
            continue
        plates.append(plate)
        ids.append(f"{plate}_{day.isoformat()}")
        result = engine_on_minutes(rows, plants)
        if result["plant_min"] > 0 or result["not_plant_min"] > 0:
            doc = engine_on_doc(plate, day, result, len(rows), now)
            ops.append(ReplaceOne({"_id": doc["_id"]}, doc, upsert=True))
        segments += overspeed_rows(plate, rows)
    replaced = 0
    if ids:
        # Besttech owns these truck/days: drop the Terminus doc (and no-minute leftovers), then write ours
        replaced = engine_on_col_delete(engine_col, ids)
        if ops:
            engine_col.bulk_write(ops, ordered=False)
        start = datetime.combine(day, datetime.min.time())
        overspeed_col.delete_many({"vehicle": {"$in": plates},
                                   "start_datetime": {"$gte": start, "$lt": start + timedelta(days=1)}})
        if segments:
            overspeed_col.insert_many(segments)
    return {"plates": len(plates), "engineon_docs": len(ops), "engineon_replaced": replaced,
            "overspeed_segments": len(segments)}


def engine_on_col_delete(engine_col, ids: list[str]) -> int:
    replaced = engine_col.count_documents({"_id": {"$in": ids}, "version_type": {"$ne": SOURCE}})
    engine_col.delete_many({"_id": {"$in": ids}})
    return replaced


def load_plants(atms_db) -> list[tuple[float, float]]:
    plants = []
    for p in atms_db["plants"].find({}, {"_id": 0, "Latitude": 1, "Longitude": 1}):
        lat, lng = _num(p.get("Latitude")), _num(p.get("Longitude"))
        if lat is not None and lng is not None:
            plants.append((lat, lng))
    if not plants:
        raise RuntimeError("atms.plants is empty — engine-on plant time cannot be told apart")
    return plants


def make_deriver(mongo_client, log):
    """Callable for series_besttech.run_days: writes engine-on + overspeed for a fetched day.
    It never raises — the fuel series of that day must still be saved."""
    analytics = mongo_client["analytics"]
    state: dict = {}

    def derive(day: date, windows) -> dict | None:
        try:
            if "plants" not in state:
                state["plants"] = load_plants(mongo_client["atms"])
            stats = write_derived(day, windows, state["plants"], analytics["summary_engineon"],
                                  analytics["overspeed"])
            log.info("besttech %s derived: %s", day.isoformat(), stats)
            return stats
        except Exception as e:   # noqa: BLE001 — reported in the job log, the series still saves
            log.warning("besttech %s engine-on/overspeed failed: %s", day.isoformat(), e)
            return {"error": str(e)}

    return derive
