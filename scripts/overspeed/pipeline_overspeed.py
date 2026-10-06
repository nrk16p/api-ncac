"""overspeed — Terminus driving_log → analytics.overspeed (spec §10.2), port of etl_overspeed_v4.py.

Default day = yesterday (Bangkok). Env (from POST /pipeline/run/overspeed or the Jobs tab):
START_DATE / END_DATE (dd/mm/YYYY), PLATES (comma list, "71-8623" or "สบ.71-8623"),
MIN_DURATION_MIN (2), MIN_RECORDS (5), GAP_MINUTES (2). OVERSPEED_COLLECTION redirects the writes
(smoke tests only; must be "overspeed" or start with "overspeed_").

Plates are read in batches of 50 through the วันที่-first index (only the five fields the segments
need; speed and distance through $getField because their names contain dots). For every batch the day's rows of each plate processed are deleted before the new segments are
inserted — including plates that no longer have a segment, so a stricter re-run leaves no stale rows.
"""
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS / "engineon"))
sys.path.insert(0, str(SCRIPTS / "fuel"))
from common import MONGODB_URI, JobLog, log, yesterday_bkk  # noqa: E402
from dates import ddmmyyyy, parse_days  # noqa: E402
from plates import terminus_plate  # noqa: E402
from pymongo import MongoClient  # noqa: E402

from overspeed_segments import DIST, PLATE, SPEED, frame_from_rows, plate_segments  # noqa: E402

INDEX = "idx_date_plate_status_order_desc"
BATCH = 50
# "ความเร็ว(กม./ชม.)" and "ระยะทาง(กม.)" contain dots: in a projection, filter or sort Mongo reads a dotted
# name as a nested path and returns nothing, so they are read literally with $getField (Mongo >= 5.0).
PROJECT = {"_id": 0, PLATE: 1, "วันที่": 1, "เวลา": 1, "speed": {"$getField": SPEED}, "dist": {"$getField": DIST}}
TARGET_RE = re.compile(r"^overspeed(_[a-z0-9_]+)?$")
RUN_ENV = ("START_DATE", "END_DATE", "PLATES", "MIN_DURATION_MIN", "MIN_RECORDS", "GAP_MINUTES", "OVERSPEED_COLLECTION")


def day_plates(values) -> list[str]:
    """Distinct driving_log plates for a day without vendor nulls/blanks, sorted (values kept as stored)."""
    return sorted({v for v in values if isinstance(v, str) and v.strip()})


def read_rows(driving_log, key: str, plates: list[str]) -> list[dict]:
    """One day's driving_log rows of `plates`, only the fields the segments need, through the วันที่-first index."""
    rows = []
    for doc in driving_log.aggregate([{"$match": {"วันที่": key, PLATE: {"$in": plates}}}, {"$project": PROJECT}],
                                     hint=INDEX):
        doc[SPEED], doc[DIST] = doc.pop("speed", None), doc.pop("dist", None)
        rows.append(doc)
    return rows


def overspeed_day(driving_log, target, day: date, plates: list[str] | None = None, gap_minutes: float = 2,
                  min_duration_min: float = 2, min_records: int = 5, batch_pause_s: float = 0.5) -> dict:
    key = ddmmyyyy(day)
    if plates:
        raw = sorted({terminus_plate(p) for p in plates})
    else:
        raw = day_plates(driving_log.distinct(PLATE, {"วันที่": key}))
    day_start = datetime.combine(day, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    stats = {"day": day.isoformat(), "plates": 0, "segments": 0, "deleted": 0}
    for i in range(0, len(raw), BATCH):
        part = raw[i:i + BATCH]
        df = frame_from_rows(read_rows(driving_log, key, part))
        if not df.empty and df[SPEED].isna().all():
            # never replace a day's rows from readings that lost their speed (e.g. a projection mistake)
            raise RuntimeError(f"{key}: driving_log rows came back without speed — overspeed rows left as they were")
        processed = sorted(df[PLATE].unique()) if not df.empty else []
        segments = []
        for _, g in df.groupby(PLATE):
            segments += plate_segments(g, gap_minutes, min_duration_min, min_records)
        if processed:
            deleted = target.delete_many({"vehicle": {"$in": processed},
                                          "start_datetime": {"$gte": day_start, "$lt": day_end}})
            stats["deleted"] += deleted.deleted_count
        if segments:
            target.insert_many(segments)
        stats["plates"] += len(processed)
        stats["segments"] += len(segments)
        if batch_pause_s and i + BATCH < len(raw):
            time.sleep(batch_pause_s)
    log.info("overspeed %s: %d plates, %d segments, %d old rows replaced",
             stats["day"], stats["plates"], stats["segments"], stats["deleted"])
    return stats


def positive_number(env: dict, name: str, default: float, cast=float):
    raw = env.get(name)
    if raw in (None, ""):
        return cast(default)
    value = cast(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {raw}")
    return value


def run_params(env: dict, yesterday: str) -> dict:
    target = env.get("OVERSPEED_COLLECTION", "overspeed")
    if not TARGET_RE.match(target):
        raise ValueError(f"OVERSPEED_COLLECTION must be 'overspeed' or 'overspeed_*', got {target!r}")
    return {
        "days": parse_days(env.get("START_DATE") or yesterday, env.get("END_DATE") or yesterday),
        "plates": [p.strip() for p in env.get("PLATES", "").split(",") if p.strip()] or None,
        "gap_minutes": positive_number(env, "GAP_MINUTES", 2),
        "min_duration_min": positive_number(env, "MIN_DURATION_MIN", 2),
        "min_records": positive_number(env, "MIN_RECORDS", 5, int),
        "target": target,
    }


def main() -> None:
    env = dict(os.environ)
    # log first, so a bad parameter shows up as a failed run on the Jobs tab card
    job = JobLog("overspeed", "overspeed", {k.lower(): env[k] for k in RUN_ENV if env.get(k)})
    try:
        params = run_params(env, yesterday_bkk().strftime("%d/%m/%Y"))
        days = params.pop("days")
        target_name = params.pop("target")
        client = MongoClient(MONGODB_URI)
        driving_log = client["terminus"]["driving_log"]
        target = client["analytics"][target_name]
        per_day = [overspeed_day(driving_log, target, day, **params) for day in days]
        job.finish("success", start_date=ddmmyyyy(days[0]), end_date=ddmmyyyy(days[-1]), target=target_name,
                   records=sum(d["segments"] for d in per_day), deleted=sum(d["deleted"] for d in per_day),
                   days=per_day)
    except Exception as e:
        job.finish("failed", error=str(e))
        raise


if __name__ == "__main__":
    main()
