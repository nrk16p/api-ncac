import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pymongo import MongoClient

router = APIRouter(prefix="/pipeline", tags=["Pipeline"])

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"

PIPELINE_SCRIPTS = {
    "ld":   SCRIPTS_DIR / "ld"   / "pipeline_ld.py",
    "scco": SCRIPTS_DIR / "scco" / "pipeline_scco.py",
    "cpac": SCRIPTS_DIR / "cpac" / "pipeline_cpac.py",
}

PIPELINE_NAMES = {"ld": "asia", "scco": "scco", "cpac": "cpac"}

# In-memory run state (single-process; reset on restart)
_running: dict[str, bool] = {k: False for k in PIPELINE_SCRIPTS}
_last_started: dict[str, str | None] = {k: None for k in PIPELINE_SCRIPTS}


def _verify_key(x_api_key: str = Header(..., alias="x-api-key")):
    key = os.getenv("PIPELINE_API_KEY")
    if not key or x_api_key != key:
        raise HTTPException(status_code=401, detail="Unauthorized")


async def _run(pipeline_type: str):
    script = PIPELINE_SCRIPTS[pipeline_type]
    _running[pipeline_type] = True
    _last_started[pipeline_type] = datetime.now(timezone.utc).isoformat()
    try:
        env = os.environ.copy()
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
    _: str = Depends(_verify_key),
):
    if pipeline_type not in PIPELINE_SCRIPTS:
        raise HTTPException(status_code=400, detail=f"Unknown pipeline: {pipeline_type}")
    if _running.get(pipeline_type):
        return {"status": "already_running", "pipeline": pipeline_type}
    background_tasks.add_task(_run, pipeline_type)
    return {"status": "started", "pipeline": pipeline_type}


@router.get("/status/{pipeline_type}")
async def pipeline_status(pipeline_type: str):
    if pipeline_type not in PIPELINE_SCRIPTS:
        raise HTTPException(status_code=400, detail=f"Unknown pipeline: {pipeline_type}")

    last_run = None
    try:
        uri = os.getenv("MONGODB_URI")
        if uri:
            client = MongoClient(uri, serverSelectionTimeoutMS=3000)
            db = client["atms"]
            doc = db["ldt_runs"].find_one(
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
