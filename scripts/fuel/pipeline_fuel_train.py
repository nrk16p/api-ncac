"""fuel_train (2nd of the month, 03:30 BKK) — train model v2 on queue decisions (+ old reviews as weak
labels) and promote it only if it beats the active scorer (spec §4.5). Skips while there are fewer
than 30 labels of each kind; the report lands in etl_jobs either way."""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engineon"))
from common import MONGODB_URI, JobLog, log  # noqa: E402
from pymongo import MongoClient  # noqa: E402

from backfill_terminus_reviews import review_truck_days  # noqa: E402
from events_store import EVENTS, MODELS, REVIEWS, load_active_model  # noqa: E402
from fuel_settings import load_settings  # noqa: E402
from series_build import thai_midnight_utc  # noqa: E402
from train import OLD_OK, OLD_SUSPICIOUS, build_dataset, train  # noqa: E402

LOOKBACK_DAYS = 365
EVENT_FIELDS = {"plate": 1, "date_key": 1, "start": 1, "end": 1, "kind": 1, "class": 1,
                "status": 1, "decision": 1, "features": 1}


def labelled_events(db, since: date, reviews: list[dict]) -> list[dict]:
    """Only the events build_dataset can label: queue decisions, plus the events on the truck-days of
    old reviews (a year of all events would be ~1 GB at ~2,000 a day). Through the date_status_score
    and plate_start indexes."""
    events = list(db[EVENTS].find({"date_key": {"$gte": since.isoformat()}, "status": "decided"}, EVENT_FIELDS))
    weak = [r for r in reviews if not r.get("event_id") and r.get("decision") in OLD_OK | OLD_SUSPICIOUS]
    for day, plates in sorted(review_truck_days(weak, since).items()):
        start = thai_midnight_utc(day)
        events += db[EVENTS].find({"plate": {"$in": sorted(plates)},
                                   "start": {"$gte": start, "$lt": start + timedelta(days=1)},
                                   "status": {"$ne": "decided"}}, EVENT_FIELDS)
    return events


def main() -> None:
    job = JobLog("fuel_train", "fuel_train")
    try:
        db = MongoClient(MONGODB_URI)["analytics"]
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        since = (now - timedelta(days=LOOKBACK_DAYS)).date()
        reviews = list(db[REVIEWS].find({}, {"plate": 1, "decision": 1, "start_ts": 1,
                                             "end_ts": 1, "event_id": 1}))
        events = labelled_events(db, since, reviews)
        result = train(build_dataset(events, reviews), load_active_model(db), load_settings(db), now)
        model = result.pop("model", None)
        if model:
            if model["active"]:
                db[MODELS].update_many({"active": True}, {"$set": {"active": False}})
            db[MODELS].replace_one({"_id": model["_id"]}, model, upsert=True)
        metrics = result.pop("metrics", {})
        log.info("fuel_train: %s %s", result, metrics)
        job.finish("success", **result, **metrics)
    except Exception as e:
        job.finish("failed", error=str(e))
        raise


if __name__ == "__main__":
    main()
