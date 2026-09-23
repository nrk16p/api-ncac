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
    "atms_pr_quick": SCRIPTS_DIR / "atms_procurement" / "pipeline_atms_pr_quick.py",
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
                  "atms_pr_quick": "atms_pr_quick",
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
    "atms_pr_quick": ("atms", "procurement_runs"),
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


# ── webhook หลัง pipeline สำเร็จ ─────────────────────────────────────────────
# pipeline ที่ป้อนข้อมูลให้ระบบปลายทางซึ่งต้อง "คำนวณใหม่ทันทีที่ข้อมูลลง" — ยิงต่อท้ายตรงนี้ดีกว่า
# ให้ปลายทางตั้ง cron เดาเวลาเอา เพราะ pipeline ใช้เวลาไม่คงที่ (ATMS ช้า/เร็วได้) นาฬิกาจึงเดาไม่ตรง
#
# atms_stockmovement* → เขียน atms.stockmovement_v5 ซึ่งเป็นต้นทางของ snapshot หน้า /safety-stock
# ใน mena-wms · mena-wms อยู่บน Vercel Hobby ที่ตั้ง cron ได้แค่ 2 ตัว (เต็มแล้ว) และยิงได้วันละครั้ง
# เรียกจากที่นี่แทนจึงได้ 5 รอบ/วัน (05:00 full + 08:30/12:30/16:30/20:30 light) ตรงจังหวะข้อมูลลงจริง
# ปลายทางที่เรียกคือ /api/cron/safety-stock-build ซึ่งอ่าน Mongo ล้วน ไม่ยิง ATMS ซ้ำ
#
# ค่าเป็น (URL_ENV, TOKEN_ENV[, SOURCE]) — ไม่ตั้ง env = ไม่ยิง เครื่อง dev หรือ instance อื่นจึงไม่ไปกวนปลายทาง
# SOURCE (ถ้ามี) เขียนทับ ?source= ใน URL — atms_pr_quick ต้องเป็น pr-hourly ไม่งั้นแถบ "รอบอัปเดตวันนี้"
# ของ mena-wms จะเอารอบรายชั่วโมงไปแย่งช่องในตาราง (เช่น 10:15 แย่งช่อง 10:00)
POST_RUN_WEBHOOKS = {
    "atms_stockmovement":       ("SAFETY_STOCK_BUILD_URL", "SAFETY_STOCK_BUILD_TOKEN"),
    "atms_stockmovement_light": ("SAFETY_STOCK_BUILD_URL", "SAFETY_STOCK_BUILD_TOKEN"),
    "atms_pr_quick":            ("SAFETY_STOCK_BUILD_URL", "SAFETY_STOCK_BUILD_TOKEN", "pr-hourly"),
}

# build ฝั่ง mena-wms เดินทีละคลังบน stockmovement_v5 (~476k แถว) ภายใต้ maxDuration 300s ของมันเอง
# ตั้ง timeout ยาวกว่านั้นนิดเดียว เพื่อให้ฝั่งโน้นเป็นคนตัดสินใจหมดเวลาเอง ไม่ใช่เราตัดสายทิ้งกลางคัน
_WEBHOOK_TIMEOUT_S = 310


def _call_webhook(url: str, token: str | None) -> tuple[int, str]:
    import requests

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.get(url, headers=headers, timeout=_WEBHOOK_TIMEOUT_S)
    return r.status_code, r.text[:500]


async def _fire_post_run_webhook(pipeline_type: str) -> None:
    """ยิง webhook ปลายทางหลัง pipeline สำเร็จ

    พังยังไงก็ต้องไม่ทำให้ pipeline กลายเป็น fail — งานหลัก (เขียนข้อมูลลง Mongo) สำเร็จไปแล้ว
    ปลายทางคำนวณไม่ทันเป็นเรื่องรองที่รอรอบหน้าได้ ห้าม raise ออกไปถึง _run เด็ดขาด
    """
    import logging

    hook = POST_RUN_WEBHOOKS.get(pipeline_type)
    if not hook:
        return
    url_env, token_env = hook[0], hook[1]
    url = os.getenv(url_env)
    if not url:
        return
    if len(hook) > 2:
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
        parts = urlsplit(url)
        q = dict(parse_qsl(parts.query))
        q["source"] = hook[2]
        url = urlunsplit(parts._replace(query=urlencode(q)))

    log = logging.getLogger(__name__)
    try:
        # requests เป็น blocking I/O — ต้องออกไปอยู่ thread แยก ไม่งั้น event loop ของ FastAPI
        # ค้างได้นานถึง 310 วินาที (ทั้ง API จะไม่ตอบ request ไหนเลยตลอดช่วงนั้น)
        status, body = await asyncio.to_thread(_call_webhook, url, os.getenv(token_env))
        if 200 <= status < 300:
            log.info("Post-run webhook %s -> %s OK (%s)", pipeline_type, url, status)
        else:
            log.error("Post-run webhook %s -> %s failed: %s %s", pipeline_type, url, status, body)
    except Exception as e:  # noqa: BLE001 - ห้ามให้ webhook ทำ pipeline ล้ม
        log.error("Post-run webhook %s -> %s error: %s", pipeline_type, url, e)


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
        else:
            # สำเร็จเท่านั้นจึงปลุกปลายทาง — รันล้มแล้วยิงต่อจะทำให้ปลายทางคำนวณจากข้อมูลที่ยังไม่ครบ
            await _fire_post_run_webhook(pipeline_type)
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
