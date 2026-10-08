"""rmc_compensation — CPAC fleetlink trips → site-time tiers → push to API_PUSH (spec §10.2).

Port of schedule_fuel/.../rmc_daily/rmc_daily.py. Env: DATE (YYYY-MM-DD) or START + END (inclusive,
≤ 14 days), DRY_RUN (fetch + transform only). With no dates it catches up every day after
analytics.etl_state {_id: "rmc_compensation"}.last_success_date through yesterday (≤ 14 per run),
advancing the state after each pushed day. Manual and dry runs never touch the state.
Needs POST_URL (same fleetlink endpoint as the cpac pipeline) and API_PUSH; the vehicle mapping is
read from analytics.rmc_vehicles (seed it with seed_rmc.py — it is not kept in this public repo).
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS / "engineon"))
sys.path.insert(0, str(SCRIPTS / "cpac"))
from common import MONGODB_URI, JobLog, log, now_bkk  # noqa: E402
from pipeline_cpac import VEHICLE_LIST  # noqa: E402  — same 136 fleetlink vehicles, company 1231
from pymongo import MongoClient  # noqa: E402

from rmc_client import fetch_report, push_records  # noqa: E402
from rmc_logic import RmcError, pending_days, run_days, run_mode  # noqa: E402

STATE_ID = "rmc_compensation"


def load_vehicles(db) -> list[dict]:
    vehicles = list(db["rmc_vehicles"].find({}, {"_id": 0}))
    if not vehicles:
        raise RmcError("analytics.rmc_vehicles is empty — run scripts/rmc/seed_rmc.py first")
    return vehicles


def load_last_success(db):
    doc = db["etl_state"].find_one({"_id": STATE_ID})
    if not doc or not doc.get("last_success_date"):
        raise RmcError("analytics.etl_state has no rmc_compensation.last_success_date — seed it from state.json")
    return datetime.strptime(doc["last_success_date"], "%Y-%m-%d").date()


def save_last_success(db, day) -> None:
    db["etl_state"].update_one(
        {"_id": STATE_ID},
        {"$set": {"last_success_date": day.isoformat(), "updated_at": datetime.now(timezone.utc).replace(tzinfo=None)}},
        upsert=True)
    log.info("rmc state → last_success_date %s", day)


def main() -> None:
    env = dict(os.environ)
    # log first, so a bad parameter shows up as a failed run on the Jobs tab card
    job = JobLog("rmc_compensation", "rmc_compensation",
                 {k.lower(): env[k] for k in ("DATE", "START", "END", "DRY_RUN") if env.get(k)})
    try:
        mode = run_mode(env)
        db = MongoClient(MONGODB_URI)["analytics"]
        vehicles = load_vehicles(db)
        post_url, api_push = os.getenv("POST_URL"), os.getenv("API_PUSH")
        on_success = None
        if mode["kind"] == "manual":
            days = [mode["start"] + timedelta(days=i) for i in range((mode["end"] - mode["start"]).days + 1)]
        else:
            days = pending_days(load_last_success(db), now_bkk().date())
            if not mode["dry_run"]:
                on_success = lambda d: save_last_success(db, d)  # noqa: E731
        stats = run_days(days,
                         fetch=lambda d: fetch_report(d, post_url, VEHICLE_LIST),
                         push=lambda rows: push_records(rows, api_push),
                         vehicles=vehicles, dry_run=mode["dry_run"], on_success=on_success)
        job.finish("success", mode=mode["kind"], dry_run=mode["dry_run"], records=sum(s["rows"] for s in stats),
                   days=stats)
    except Exception as e:
        job.finish("failed", error=str(e))
        raise


if __name__ == "__main__":
    main()
