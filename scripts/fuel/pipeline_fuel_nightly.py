"""fuel_nightly (04:15 BKK) — spec §3.3, §6.

1. Besttech: if the 01:30 run left no docs for yesterday, run it once more — unless that run is
   still going (the per-vehicle pull takes ~76 min; two clients on one key get throttled). A
   failure is recorded and does not stop Terminus.
2. Terminus: wait until yesterday's driving_log has at least half the usual number of trucks
   (NIGHTLY_RETRIES × NIGHTLY_WAIT_S, default 3 × 30 min), then ingest; flag terminus_partial if
   it never got there.
Part 2 appends the fuel_events step.
"""
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engineon"))
from common import MONGODB_URI, JobLog, log, yesterday_bkk  # noqa: E402
from pymongo import MongoClient  # noqa: E402

from dates import ddmmyyyy  # noqa: E402
from series_besttech import make_client, run_days  # noqa: E402
from series_store import SERIES, count_series, ensure_indexes  # noqa: E402
from series_terminus import ingest_terminus_day  # noqa: E402

READY_RATIO = 0.5
BESTTECH_RUN_MAX_HOURS = 2.5   # 01:30 + 2.5 h < 04:15, so a hard-killed run never blocks the catch-up


def terminus_ready(plates_today: int, plates_recent: list[int], ratio: float = READY_RATIO) -> bool:
    """Ready when today's truck count reaches `ratio` × the recent daily average (any count with no history)."""
    recent = [count for count in plates_recent if count > 0]
    if not recent:
        return plates_today > 0
    return plates_today >= ratio * (sum(recent) / len(recent))


def besttech_run_in_progress(latest_job: dict | None, now: datetime,
                             max_hours: float = BESTTECH_RUN_MAX_HOURS) -> bool:
    """True when the latest fuel_series_besttech etl_jobs entry is still running and started recently
    (an entry left "running" by a crashed process stops counting after `max_hours`)."""
    if not latest_job or latest_job.get("status") != "running":
        return False
    started = latest_job.get("created_at")
    return started is not None and now - started < timedelta(hours=max_hours)


def recent_terminus_counts(db, day: date, lookback: int = 7) -> list[int]:
    return [db[SERIES].count_documents({"date_key": (day - timedelta(days=i)).isoformat(),
                                        "source": "terminus", "n": {"$gt": 0}})
            for i in range(1, lookback + 1)]


def main() -> None:
    day = yesterday_bkk().date()
    retries = int(os.getenv("NIGHTLY_RETRIES", "3"))
    wait_s = float(os.getenv("NIGHTLY_WAIT_S", "1800"))
    job = JobLog("fuel_nightly", "fuel_nightly", {"day": day.isoformat()})
    result: dict = {}
    try:
        client = MongoClient(MONGODB_URI)
        db = client["analytics"]
        ensure_indexes(db)

        if count_series(db, day.isoformat(), "besttech") == 0:
            latest = db["etl_jobs"].find_one({"job_type": "fuel_series_besttech"}, sort=[("created_at", -1)])
            if besttech_run_in_progress(latest, datetime.now(timezone.utc).replace(tzinfo=None)):
                result["besttech_catchup_skipped"] = "01:30 run still in progress"
            else:
                try:
                    result["besttech_catchup_docs"] = run_days(make_client(), db, [day])
                except Exception as e:  # Terminus must still run when Besttech is down
                    log.error("besttech catch-up failed: %s", e)
                    result["besttech_error"] = str(e)

        driving_log = client["terminus"]["driving_log"]
        recent = recent_terminus_counts(db, day)
        today = 0
        for attempt in range(retries + 1):
            today = len(driving_log.distinct("ทะเบียนพาหนะ", {"วันที่": ddmmyyyy(day)}))
            if terminus_ready(today, recent) or attempt == retries:
                break
            log.info("terminus %s not ready (%d trucks) — waiting %.0fs", day, today, wait_s)
            time.sleep(wait_s)
        result["terminus_partial"] = not terminus_ready(today, recent)
        result["terminus_docs"] = ingest_terminus_day(client["terminus"], db, day)
        job.finish("success", **result)
    except Exception as e:
        job.finish("failed", error=str(e), **result)
        raise


if __name__ == "__main__":
    main()
