"""fuel_places (Mondays 01:00 BKK) — known places for the "at a place" evidence (spec §4.3):
atms.plants (300 m circles) + Besttech /location POIs. Replaces the whole collection each run; if
Besttech is down the plants are still written, last run's POIs are kept and the error is recorded."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engineon"))
from common import MONGODB_URI, JobLog, log  # noqa: E402
from pymongo import MongoClient, ReplaceOne  # noqa: E402

from events_store import PLACES  # noqa: E402
from places import besttech_places, plant_places  # noqa: E402
from series_besttech import make_client  # noqa: E402


def refresh_places(client, besttech) -> dict:
    """Rewrite analytics.fuel_places from atms.plants and `besttech.location()` (errors there only
    drop the POIs). Returns counts for the job log."""
    db = client["analytics"]
    rows = list(client["atms"]["plants"].find({}, {"_id": 0, "client": 1, "plant_code": 1,
                                                    "Latitude": 1, "Longitude": 1}))
    places = plant_places(rows)
    result: dict = {"plants": len(places)}
    try:
        pois = besttech_places(besttech.location())
        places += pois
        result["besttech_pois"] = len(pois)
    except Exception as e:  # plants are still worth writing
        log.error("besttech /location failed: %s", e)
        result["besttech_error"] = str(e)
    unique = {p["_id"]: p for p in places}
    if unique:
        db[PLACES].bulk_write([ReplaceOne({"_id": k}, v, upsert=True) for k, v in unique.items()], ordered=False)
        stale = {"_id": {"$nin": list(unique)}}
        if "besttech_error" in result:   # keep last week's POIs rather than none
            stale["kind"] = "plant"
        db[PLACES].delete_many(stale)
    result["records"] = len(unique)
    return result


def main() -> None:
    job = JobLog("fuel_places", "fuel_places")
    try:
        job.finish("success", **refresh_places(MongoClient(MONGODB_URI), make_client()))
    except Exception as e:
        job.finish("failed", error=str(e))
        raise


if __name__ == "__main__":
    main()
