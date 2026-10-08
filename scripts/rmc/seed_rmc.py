"""One-off: load the CPAC vehicle mapping (vehicle.json) into analytics.rmc_vehicles.

    .venv/bin/python scripts/rmc/seed_rmc.py PATH/TO/vehicle.json [--state PATH/TO/state.json]

--state also seeds analytics.etl_state {_id: "rmc_compensation"} — a cutover step (spec §10.6), only
with the user's go-ahead. The mapping holds driver names, which is why it lives in Mongo and not in
this public repo.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engineon"))
from common import MONGODB_URI, log  # noqa: E402
from pymongo import ASCENDING, MongoClient  # noqa: E402

FIELDS = ("id", "code", "plate_no", "plate_no_only", "driver_name", "driver_id", "device_types_id")


def vehicle_docs(data: dict) -> list[dict]:
    rows = data.get("data") if isinstance(data, dict) else None
    if not rows:
        raise ValueError("vehicle.json has no 'data' rows")
    return [{k: row.get(k) for k in FIELDS} for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("vehicle_json")
    parser.add_argument("--state")
    args = parser.parse_args()
    docs = vehicle_docs(json.loads(Path(args.vehicle_json).read_text(encoding="utf-8")))
    db = MongoClient(MONGODB_URI)["analytics"]
    db["rmc_vehicles"].delete_many({})
    db["rmc_vehicles"].insert_many(docs)
    db["rmc_vehicles"].create_index([("code", ASCENDING)], name="code")
    log.info("rmc_vehicles: %d rows", len(docs))
    if args.state:
        last = json.loads(Path(args.state).read_text(encoding="utf-8"))["last_success_date"]
        datetime.strptime(last, "%Y-%m-%d")
        db["etl_state"].update_one({"_id": "rmc_compensation"},
                                   {"$set": {"last_success_date": last,
                                             "updated_at": datetime.now(timezone.utc).replace(tzinfo=None)}},
                                   upsert=True)
        log.info("etl_state rmc_compensation → %s", last)


if __name__ == "__main__":
    main()
