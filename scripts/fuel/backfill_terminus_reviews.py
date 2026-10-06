"""One-off: Terminus gps_series for the truck-days behind analytics.fuel_drop_reviews (spec §3.5).

These become the weak training labels in Part 2. Terminus raw data starts 2026-03-01, so earlier
review windows are clamped (and windows that end before then are skipped).
Run locally: .venv/bin/python scripts/fuel/backfill_terminus_reviews.py
"""
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engineon"))
from common import MONGODB_URI, JobLog, log  # noqa: E402
from pymongo import MongoClient  # noqa: E402

from plates import normalize_plate  # noqa: E402
from series_store import ensure_indexes, load_tanks  # noqa: E402
from series_terminus import ingest_terminus_day  # noqa: E402

TH_TZ = timezone(timedelta(hours=7))
TERMINUS_START = date(2026, 3, 1)


def review_truck_days(reviews: list[dict], first_day: date = TERMINUS_START) -> dict[date, set[str]]:
    days: dict[date, set[str]] = defaultdict(set)
    for review in reviews:
        plate = normalize_plate(review.get("plate"))
        start_ts, end_ts = review.get("start_ts"), review.get("end_ts")
        if not plate or start_ts is None or end_ts is None:
            continue
        day = max(datetime.fromtimestamp(start_ts / 1000, TH_TZ).date(), first_day)
        last = datetime.fromtimestamp(end_ts / 1000, TH_TZ).date()
        while day <= last:
            days[day].add(plate)
            day += timedelta(days=1)
    return dict(days)


def main() -> None:
    job = JobLog("fuel_backfill_reviews", "fuel_backfill_reviews")
    try:
        client = MongoClient(MONGODB_URI)
        db = client["analytics"]
        ensure_indexes(db)
        reviews = list(db["fuel_drop_reviews"].find({}, {"plate": 1, "start_ts": 1, "end_ts": 1}))
        plan = review_truck_days(reviews)
        tanks = load_tanks(db)
        written = 0
        for day in sorted(plan):
            written += ingest_terminus_day(client["terminus"], db, day, plates=sorted(plan[day]), tanks=tanks)
        log.info("review backfill: %d days, %d docs", len(plan), written)
        job.finish("success", records=written, days=len(plan),
                   truck_days=sum(len(p) for p in plan.values()))
    except Exception as e:
        job.finish("failed", error=str(e))
        raise


if __name__ == "__main__":
    main()
