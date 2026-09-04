import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Header, HTTPException
from pymongo import MongoClient

router = APIRouter(prefix="/pipeline", tags=["Pipeline"])

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"

PIPELINE_SCRIPTS = {
    "ld":   SCRIPTS_DIR / "ld"   / "pipeline_ld.py",
    "scco": SCRIPTS_DIR / "scco" / "pipeline_scco.py",
    "cpac": SCRIPTS_DIR / "cpac" / "pipeline_cpac.py",
    "deliver_result": SCRIPTS_DIR / "deliver_result" / "pipeline_deliver_result.py",
    "driver_cost": SCRIPTS_DIR / "driver_cost" / "pipeline_driver_cost.py",
    "atms_procurement": SCRIPTS_DIR / "atms_procurement" / "pipeline_atms_procurement.py",
    "atms_procurement_light": SCRIPTS_DIR / "atms_procurement" / "pipeline_atms_procurement_light.py",
    "atms_audit": SCRIPTS_DIR / "atms_procurement" / "pipeline_atms_audit.py",
    "atms_supplier": SCRIPTS_DIR / "atms_procurement" / "pipeline_atms_supplier.py",
    "engineon": SCRIPTS_DIR / "engineon" / "pipeline_engineon.py",
    "drivercost_ticket": SCRIPTS_DIR / "engineon" / "pipeline_drivercost_ticket.py",
    "engineon_trip_summary": SCRIPTS_DIR / "engineon" / "pipeline_engineon_trip_summary.py",
    "vehiclemaster": SCRIPTS_DIR / "engineon" / "pipeline_vehiclemaster.py",
    "maintenance": SCRIPTS_DIR / "maintenance" / "pipeline_maintenance.py",
    "atms_stockmovement": SCRIPTS_DIR / "atms_stockmovement" / "pipeline_atms_stockmovement.py",
    "atms_stockmovement_light": SCRIPTS_DIR / "atms_stockmovement" / "pipeline_atms_stockmovement_light.py",
}

PIPELINE_NAMES = {"ld": "asia", "scco": "scco", "cpac": "cpac",
                  "deliver_result": "deliver_result", "driver_cost": "driver_cost",
                  "atms_procurement": "atms_procurement",
                  "atms_procurement_light": "atms_procurement_light",
                  "atms_audit": "atms_audit",
                  "atms_supplier": "atms_supplier",
                  "engineon": "engineon",
                  "drivercost_ticket": "drivercost_ticket",
                  "engineon_trip_summary": "engineon_trip_summary",
                  "vehiclemaster": "vehiclemaster",
                  "maintenance": "maintenance",
                  "atms_stockmovement": "atms_stockmovement",
                  "atms_stockmovement_light": "atms_stockmovement_light"}

# Where each pipeline logs its runs: (db, collection)
RUN_LOG_LOCATION = {
    "ld":   ("atms", "ldt_runs"),
    "scco": ("atms", "ldt_runs"),
    "cpac": ("atms", "ldt_runs"),
    "deliver_result": ("mena-bi", "pipeline_runs"),
    "driver_cost": ("mena-bi", "pipeline_runs"),
    "atms_procurement": ("atms", "procurement_runs"),
    "atms_procurement_light": ("atms", "procurement_runs"),
    "atms_audit": ("atms", "deposit_audit"),
    "atms_supplier": ("atms", "procurement_runs"),
    "engineon": ("analytics", "etl_jobs"),
    "drivercost_ticket": ("analytics", "etl_jobs"),
    "engineon_trip_summary": ("analytics", "etl_jobs"),
    "vehiclemaster": ("analytics", "etl_jobs"),
    "maintenance": ("analytics", "etl_jobs"),
    "atms_stockmovement": ("atms", "stockmovement_runs"),
    "atms_stockmovement_light": ("atms", "stockmovement_runs"),
}

# In-memory run state (single-process; reset on restart)
_running: dict[str, bool] = {k: False for k in PIPELINE_SCRIPTS}
_last_started: dict[str, str | None] = {k: None for k in PIPELINE_SCRIPTS}


def _verify_key(x_api_key: str = Header(..., alias="x-api-key")):
    key = os.getenv("PIPELINE_API_KEY")
    if not key or x_api_key != key:
        raise HTTPException(status_code=401, detail="Unauthorized")


async def _run(pipeline_type: str, params: dict | None = None):
    script = PIPELINE_SCRIPTS[pipeline_type]
    _running[pipeline_type] = True
    _last_started[pipeline_type] = datetime.now(timezone.utc).isoformat()
    try:
        env = os.environ.copy()
        # Optional run params (e.g. {"year": 2026, "month": 7}) become UPPERCASE env
        # vars for the subprocess — scripts fall back to sensible defaults without them
        if params:
            env.update({str(k).upper(): str(v) for k, v in params.items() if v is not None})
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(script),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            import logging
            logging.error(
                "Pipeline %s failed (exit %s):\n%s",
                pipeline_type, proc.returncode,
                stderr.decode(errors="replace")[-2000:],
            )
    finally:
        _running[pipeline_type] = False


@router.post("/run/{pipeline_type}", status_code=202)
async def run_pipeline(
    pipeline_type: str,
    background_tasks: BackgroundTasks,
    params: dict | None = Body(default=None),
    _: str = Depends(_verify_key),
):
    if pipeline_type not in PIPELINE_SCRIPTS:
        raise HTTPException(status_code=400, detail=f"Unknown pipeline: {pipeline_type}")
    if _running.get(pipeline_type):
        return {"status": "already_running", "pipeline": pipeline_type}
    background_tasks.add_task(_run, pipeline_type, params)
    return {"status": "started", "pipeline": pipeline_type, "params": params}


@router.get("/status/{pipeline_type}")
async def pipeline_status(pipeline_type: str):
    if pipeline_type not in PIPELINE_SCRIPTS:
        raise HTTPException(status_code=400, detail=f"Unknown pipeline: {pipeline_type}")

    last_run = None
    try:
        uri = os.getenv("MONGODB_URI")
        if uri:
            client = MongoClient(uri, serverSelectionTimeoutMS=3000)
            db_name, coll_name = RUN_LOG_LOCATION[pipeline_type]
            db = client[db_name]
            doc = db[coll_name].find_one(
                {"pipeline": PIPELINE_NAMES[pipeline_type]},
                sort=[("created_at", -1)],
                projection={"ldt_rows": 0, "new_ship_to_rows": 0},
            )
            if doc:
                doc["_id"] = str(doc["_id"])
                last_run = doc
            client.close()
    except Exception:
        pass

    return {
        "pipeline": pipeline_type,
        "running": _running.get(pipeline_type, False),
        "last_started": _last_started.get(pipeline_type),
        "last_run": last_run,
    }
