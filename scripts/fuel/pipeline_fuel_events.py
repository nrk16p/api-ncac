"""fuel_events — detect, score and store one day's fuel events (spec §4). Runs as the last step of
fuel_nightly; registered on its own for re-runs: START_DATE / END_DATE (dd/mm/YYYY), default yesterday.

Pass 1: every truck-day with enough valid fuel minutes → burn-rate observations (fuel_day_stats).
Baselines: median of the truck's last `baseline_days` of observations (today included), fleet median
below `baseline_min_days`. Pass 2: candidates → evidence → merge boxes → score → re-run plan → store
events and the daily summary.
"""
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engineon"))
from common import MONGODB_URI, JobLog, log, yesterday_bkk  # noqa: E402
from pymongo import MongoClient  # noqa: E402

from baseline import day_rates, fleet_baseline, truck_baseline  # noqa: E402
from dates import parse_days  # noqa: E402
from detect import day_series, find_candidates, prepare  # noqa: E402
from events import daily_summary, merge_sources, plan_rerun, raw_event, score_event  # noqa: E402
from events_store import (GPS_DB, drivers_for, ensure_event_indexes, history_counts,  # noqa: E402
                          load_active_model, load_day_stats, load_existing_events, load_places,
                          save_day_stats, save_summary, vehicles_for, write_events)
from features import day_evidence, evidence  # noqa: E402
from fuel_settings import load_settings  # noqa: E402
from series_store import SERIES  # noqa: E402


def run_day(client, day: date, now: datetime | None = None) -> dict:
    db = client["analytics"]
    ensure_event_indexes(db)
    settings = load_settings(db)
    key = day.isoformat()
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    docs = list(db[SERIES].find({"date_key": key}))

    prepared, stats_today = [], []
    for doc in docs:
        series = day_series(doc)
        if series is None or series.valid_share < settings["sparse_share"]:
            continue
        ctx = prepare(series, settings)
        prepared.append((doc, series, ctx))
        stats_today.append({"_id": f"{doc['plate']}|{key}|{doc['source']}", "plate": doc["plate"],
                            "source": doc["source"], "date_key": key, **day_rates(series, ctx)})

    window_from = (day - timedelta(days=settings["baseline_days"] - 1)).isoformat()
    history = [s for s in load_day_stats(db, window_from, key) if s["date_key"] != key] + stats_today
    fleet = fleet_baseline(history)
    per_plate = defaultdict(list)
    for s in history:
        per_plate[s["plate"]].append(s)

    places = load_places(db)
    raw = []
    for doc, series, ctx in prepared:
        base = truck_baseline(per_plate[doc["plate"]], fleet, settings["baseline_min_days"])
        day_ev = day_evidence(series, ctx, settings)
        for candidate in find_candidates(series, ctx, settings):
            ev = evidence(series, ctx, candidate, base, settings, places, day_ev)
            raw.append(raw_event(doc["plate"], doc.get("truck_code"), key, series.status, ev))

    model = load_active_model(db)
    plates_hist, drivers_hist = history_counts(db, key)
    plates = [d["plate"] for d in docs]
    drivers = drivers_for(db, plates, key)
    vehicles = vehicles_for(client[GPS_DB], plates, key)
    scored = []
    for r in merge_sources(raw, settings["merge_min"]):
        driver = drivers.get(r["plate"])
        r["features"]["truck_confirmed_30d"] = plates_hist.get(r["plate"], 0)
        r["features"]["driver_confirmed_30d"] = drivers_hist.get(driver, 0) if driver else 0
        scored.append(score_event(r, settings, model, driver, now, vehicles.get(r["plate"])))

    existing = load_existing_events(db, key)
    upserts, stale, delete = plan_rerun(scored, existing)
    write_events(db, upserts, stale, delete, now)
    save_day_stats(db, stats_today)
    kept = upserts + [e for e in existing if e["_id"] in set(stale)]
    summary = daily_summary(key, docs, kept, settings, now)
    save_summary(db, summary)
    log.info("fuel_events %s: %d events (%d open, %d auto-closed, %d audit), %d stale, %d deleted",
             key, len(upserts), summary["open"], summary["auto_closed"], summary["audit"], len(stale), len(delete))
    return {"events": len(upserts), "open": summary["open"], "auto_closed": summary["auto_closed"],
            "audit": summary["audit"], "stale": len(stale), "deleted": len(delete),
            "scorer": model["version"] if model else "rules-v1"}


def main() -> None:
    yesterday = yesterday_bkk().strftime("%d/%m/%Y")
    start, end = os.getenv("START_DATE", yesterday), os.getenv("END_DATE", yesterday)
    job = JobLog("fuel_events", "fuel_events", {"start_date": start, "end_date": end})
    try:
        client = MongoClient(MONGODB_URI)
        totals: dict = defaultdict(int)
        for day in parse_days(start, end):
            for k, v in run_day(client, day).items():
                if isinstance(v, int):
                    totals[k] += v
        job.finish("success", records=totals["events"], **{k: v for k, v in totals.items() if k != "events"})
    except Exception as e:
        job.finish("failed", error=str(e))
        raise


if __name__ == "__main__":
    main()
