"""Vehicle-master pipeline — scrapes the ATMS vehicle export table into atms.vehiclemaster
(full replace). Manual trigger only (no cron) — the master changes rarely.

Ported from api-engineon app/etl_vehiclemaster.py, with ATMS auto-login.
"""
import io
import warnings

import pandas as pd
from pymongo import MongoClient

from common import BASE_URL, MONGODB_URI, JobLog, atms_session, log

warnings.filterwarnings("ignore")

EXPORT_URL = (
    f"{BASE_URL}/veh/vehicle/index.export/"
    "?page=1&order_by=v.code%20asc&search-toggle-status=&order_by=v.code%20asc"
)


def run() -> int:
    s = atms_session()
    resp = s.get(EXPORT_URL, timeout=60)
    resp.raise_for_status()
    if not resp.encoding:
        resp.encoding = resp.apparent_encoding

    tables = pd.read_html(io.StringIO(resp.text), displayed_only=False)
    if not tables:
        raise RuntimeError("No HTML tables found in vehicle master response")

    df = max(tables, key=lambda t: t.shape[0] * t.shape[1])
    df.columns = df.columns.map(lambda c: str(c).strip())
    df = df.loc[:, ~df.columns.astype(str).str.contains(r"^Unnamed", case=False)]
    df = df.astype(str)

    records = df.to_dict(orient="records")
    if not records:
        raise RuntimeError("Parsed 0 vehicle master rows — refusing to wipe collection")

    col = MongoClient(MONGODB_URI)["atms"]["vehiclemaster"]
    deleted = col.delete_many({}).deleted_count
    col.insert_many(records)
    log.info("replaced %d old → inserted %d rows (atms.vehiclemaster)", deleted, len(records))
    return len(records)


def main():
    job = JobLog("vehiclemaster", "vehiclemaster")
    try:
        n = run()
        job.finish("success", rows=n)
    except Exception as e:
        job.finish("failed", error=str(e))
        raise


if __name__ == "__main__":
    main()
