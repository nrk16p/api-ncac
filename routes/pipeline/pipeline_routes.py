import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Header, HTTPException
from fastapi.encoders import jsonable_encoder
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
    "fuel_series_besttech": SCRIPTS_DIR / "fuel" / "series_besttech.py",
    "fuel_series_terminus": SCRIPTS_DIR / "fuel" / "series_terminus.py",
    "fuel_nightly": SCRIPTS_DIR / "fuel" / "pipeline_fuel_nightly.py",
    "fuel_tanks": SCRIPTS_DIR / "fuel" / "pipeline_fuel_tanks.py",
    "fuel_events": SCRIPTS_DIR / "fuel" / "pipeline_fuel_events.py",
    "fuel_places": SCRIPTS_DIR / "fuel" / "pipeline_fuel_places.py",
    "fuel_train": SCRIPTS_DIR / "fuel" / "pipeline_fuel_train.py",
    "maintenance": SCRIPTS_DIR / "maintenance" / "pipeline_maintenance.py",
    "atms_stockmovement": SCRIPTS_DIR / "atms_stockmovement" / "pipeline_atms_stockmovement.py",
    "atms_stockmovement_light": SCRIPTS_DIR / "atms_stockmovement" / "pipeline_atms_stockmovement_light.py",
    "overspeed": SCRIPTS_DIR / "overspeed" / "pipeline_overspeed.py",
    "rmc_compensation": SCRIPTS_DIR / "rmc" / "pipeline_rmc.py",
    "store-withdraw-drift": SCRIPTS_DIR / "store_withdraw" / "pipeline_store_withdraw_drift.py",
    "core-return-sync": SCRIPTS_DIR / "core_return" / "pipeline_core_return_sync.py",
    "vehicle_daily": SCRIPTS_DIR / "vehicle_daily" / "pipeline_vehicle_daily.py",
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
                  "fuel_series_besttech": "fuel_series_besttech",
                  "fuel_series_terminus": "fuel_series_terminus",
                  "fuel_nightly": "fuel_nightly",
                  "fuel_tanks": "fuel_tanks",
                  "fuel_events": "fuel_events",
                  "fuel_places": "fuel_places",
                  "fuel_train": "fuel_train",
                  "maintenance": "maintenance",
                  "atms_stockmovement": "atms_stockmovement",
                  "atms_stockmovement_light": "atms_stockmovement_light",
                  "overspeed": "overspeed",
                  "rmc_compensation": "rmc_compensation",
                  "store-withdraw-drift": "store-withdraw-drift",
                  "core-return-sync": "core-return-sync",
                  "vehicle_daily": "vehicle_daily"}

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
    "fuel_series_besttech": ("analytics", "etl_jobs"),
    "fuel_series_terminus": ("analytics", "etl_jobs"),
    "fuel_nightly": ("analytics", "etl_jobs"),
    "fuel_tanks": ("analytics", "etl_jobs"),
    "fuel_events": ("analytics", "etl_jobs"),
    "fuel_places": ("analytics", "etl_jobs"),
    "fuel_train": ("analytics", "etl_jobs"),
    "maintenance": ("analytics", "etl_jobs"),
    "atms_stockmovement": ("atms", "stockmovement_runs"),
    "atms_stockmovement_light": ("atms", "stockmovement_runs"),
    "overspeed": ("analytics", "etl_jobs"),
    "rmc_compensation": ("analytics", "etl_jobs"),
    "store-withdraw-drift": ("analytics", "etl_jobs"),
    "core-return-sync": ("analytics", "etl_jobs"),
    "vehicle_daily": ("analytics", "etl_jobs"),
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

# pipeline ที่ต้อง sync คงเหลือ/min/max จาก ATMS (mena-wms /api/cron/atms-sku-sync) ก่อนยิง build
# atms_pr_quick รันทุกชั่วโมง 07:15–20:15 — เดิมคงเหลือ/min/max ขยับแค่ 06:00 กับ 10:00 แก้ min ใน ATMS ตอนบ่าย
# หน้า /safety-stock ยังโชว์ค่าเก่าถึงเช้าวันรุ่งขึ้น (ผู้ใช้ขอให้ถี่ขึ้น 17/11/2026) · sync ล้มก็ยัง build ต่อ
# เพราะรายการ PR ที่เพิ่งดึงมายังสดอยู่
SYNC_BEFORE_BUILD = {"atms_pr_quick"}

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
    if pipeline_type in SYNC_BEFORE_BUILD:
        urls = _wms_urls()
        if urls:
            try:
                status, body = await asyncio.to_thread(_call_webhook, urls[0], urls[2])
                if not 200 <= status < 300:
                    log.error("Pre-build sku sync %s failed: %s %s", pipeline_type, status, body)
            except Exception as e:  # noqa: BLE001 - sync ล้มก็ยัง build ต่อ
                log.error("Pre-build sku sync %s error: %s", pipeline_type, e)
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


# ── รอบเช้า: sync คงเหลือ/min/max จาก ATMS ให้ /safety-stock ของ mena-wms ─────────────────
# เดิม mena-wms ดึงคงเหลือจาก ATMS วันละครั้งตอน 10:00 (cron Vercel ซึ่งเต็ม 2 ตัวแล้ว) ของที่รับเข้าหลัง 10:00
# จึงขึ้น "ของหมด" ไปจนถึง 10:00 อีกวัน (เคส LB05AE00133, 2026-10-20) → ให้ที่นี่ยิงเพิ่มตอน 05:55 ไทย
# ใช้ env ชุดเดียวกับ webhook ด้านบน (token = CRON_SECRET ของ mena-wms ใช้ได้ทั้งสอง route)
# ไม่ส่ง ?inventory= เพื่อให้ mena-wms ตัดสินเองว่าครอบคลุมคลังไหน (WAREHOUSES ใน safety-stock-core.ts)
_wms_sync_running = False


def _wms_urls() -> tuple[str, str, str | None] | None:
    from urllib.parse import urlsplit, urlunsplit

    build_url = os.getenv("SAFETY_STOCK_BUILD_URL")
    if not build_url:
        return None
    parts = urlsplit(build_url)
    sync_path = parts.path.replace("/safety-stock-build", "/atms-sku-sync")
    sync_url = urlunsplit(parts._replace(path=sync_path, query=""))
    build = urlunsplit(parts._replace(query="source=morning-sync"))
    return sync_url, build, os.getenv("SAFETY_STOCK_BUILD_TOKEN")


async def run_wms_sku_sync_morning() -> None:
    """sync คงเหลือจาก ATMS แล้วสั่ง build ต่อ — sync ล้มก็ไม่ build (คำนวณจากยอดเก่าไม่มีประโยชน์)
    พังยังไงก็แค่ log ห้าม raise ออกไปถึง scheduler"""
    import logging

    global _wms_sync_running
    log = logging.getLogger(__name__)
    urls = _wms_urls()
    if not urls:
        return
    if _wms_sync_running:
        log.warning("wms_sku_sync_morning: รอบก่อนยังไม่จบ ข้ามรอบนี้")
        return
    sync_url, build_url, token = urls
    _wms_sync_running = True
    try:
        status, body = await asyncio.to_thread(_call_webhook, sync_url, token)
        if not 200 <= status < 300:
            log.error("wms_sku_sync_morning sync failed: %s %s", status, body)
            return
        status, body = await asyncio.to_thread(_call_webhook, build_url, token)
        if 200 <= status < 300:
            log.info("wms_sku_sync_morning OK")
        else:
            log.error("wms_sku_sync_morning build failed: %s %s", status, body)
    except Exception as e:  # noqa: BLE001
        log.error("wms_sku_sync_morning error: %s", e)
    finally:
        _wms_sync_running = False


def _verify_key(x_api_key: str = Header(..., alias="x-api-key")):
    key = os.getenv("PIPELINE_API_KEY")
    if not key or x_api_key != key:
        raise HTTPException(status_code=401, detail="Unauthorized")


async def _run(pipeline_type: str, params: dict | None = None, fire_webhook: bool = True) -> bool:
    """รัน pipeline เป็น subprocess · คืน True เมื่อ exit 0
    fire_webhook=False ใช้กับ live sync ที่สั่ง build เองตอนท้าย ไม่ต้องให้ build ทุกคลังซ้ำกลางทาง"""
    script = PIPELINE_SCRIPTS[pipeline_type]
    ok = False
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
            ok = True
            # สำเร็จเท่านั้นจึงปลุกปลายทาง — รันล้มแล้วยิงต่อจะทำให้ปลายทางคำนวณจากข้อมูลที่ยังไม่ครบ
            if fire_webhook:
                await _fire_post_run_webhook(pipeline_type)
    finally:
        _running[pipeline_type] = False
    return ok


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


# ── live sync: ปุ่ม "ดึงข้อมูลล่าสุดจาก ATMS" ในหน้า /safety-stock ของ mena-wms ─────────────
# ผู้ใช้กดแล้วไม่อยากรอรอบถัดไป · ทำต่อกันที่นี่ทั้งสาย (ปิดหน้าเว็บไปก็ยังเดินต่อ) แทนให้เบราว์เซอร์คุมจังหวะ
#   1. sku    — mena-wms ดึงคงเหลือ/min/max ของคลังนั้นจาก ATMS
#   2. build1 — คำนวณคลังนั้นทันที ผู้ใช้เห็นคงเหลือใหม่ในราว 1 นาที ไม่ต้องรอขั้นที่เหลือ
#   3. movement    — atms_stockmovement_light ย้อนพอครอบ N วัน (stockmovement ดึงเป็นรายเดือน)
#   4. procurement — atms_procurement_light หน้าต่าง N วัน (PR/PO/DD = ตัวเลข "กำลังสั่งซื้อ")
#   5. build2 — คำนวณคลังนั้นอีกรอบจากข้อมูลครบชุด
# ทีละขั้น ไม่ยิง ATMS พร้อมกัน (ยิงรายงานพร้อมกันแล้ว ATMS 500) · ทั้งระบบรันได้ทีละรอบ
# pipeline ที่รอบตั้งเวลากำลังรันอยู่แล้ว = รอให้จบแล้วใช้ผลนั้น ไม่สั่งซ้ำ
# สถานะเก็บในหน่วยความจำ — Render restart แล้วหาย ยอมรับได้ (ฝั่ง mena-wms เห็นเป็น "ไม่รู้ผล" แล้วกดใหม่ได้)
LIVE_SYNC_STEPS = ["sku", "build1", "movement", "procurement", "build2"]
LIVE_SYNC_MAX_DAYS = 31
_live_sync: dict = {}


def _live_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _wait_not_running(pipeline_type: str, max_wait_s: int = 1200) -> bool:
    """รอบตั้งเวลากำลังรัน pipeline นี้อยู่ → รอจนจบ (คืน True = รอแล้ว ข้อมูลสดพอ ไม่ต้องรันซ้ำ)"""
    if not _running.get(pipeline_type):
        return False
    waited = 0
    while _running.get(pipeline_type) and waited < max_wait_s:
        await asyncio.sleep(5)
        waited += 5
    return True


def _live_windows(days: int, ict_now: datetime) -> tuple[int, str]:
    """(SM_MONTHS, ATMS_FROM_DATE) ที่ครอบ `days` วันล่าสุด — stockmovement ดึงทั้งเดือน จึงนับจำนวนเดือนที่แตะ"""
    from datetime import timedelta

    start = ict_now - timedelta(days=days)
    months = (ict_now.year - start.year) * 12 + (ict_now.month - start.month) + 1
    return months, start.strftime("%d/%m/%Y")


async def run_wms_live_sync(inventory: str, days: int, user: str | None) -> None:
    import logging
    from datetime import timedelta
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    log = logging.getLogger(__name__)
    st = _live_sync
    urls = _wms_urls()

    def step(name: str, status: str, note: str | None = None) -> None:
        s = st["steps"][name]
        s["status"] = status
        if status == "running":
            s["startedAt"] = _live_now()
        else:
            s["finishedAt"] = _live_now()
        if note:
            s["note"] = note

    def with_query(url: str, **q: str) -> str:
        parts = urlsplit(url)
        cur = dict(parse_qsl(parts.query))
        cur.update(q)
        return urlunsplit(parts._replace(query=urlencode(cur)))

    async def wms(url: str) -> tuple[bool, str]:
        status, body = await asyncio.to_thread(_call_webhook, url, urls[2])
        return 200 <= status < 300, f"{status} {body[:200]}"

    try:
        if not urls:
            raise RuntimeError("ยังไม่ได้ตั้ง SAFETY_STOCK_BUILD_URL")
        sync_url, build_url, _ = urls
        build_url = with_query(build_url, source="live-sync", inventory=inventory)
        sm_months, from_date = _live_windows(days, datetime.utcnow() + timedelta(hours=7))

        step("sku", "running")
        ok, info = await wms(with_query(sync_url, inventory=inventory))
        step("sku", "ok" if ok else "error", None if ok else info)

        step("build1", "running")
        ok, info = await wms(build_url)
        step("build1", "ok" if ok else "error", None if ok else info)

        step("movement", "running")
        if await _wait_not_running("atms_stockmovement_light") or await _wait_not_running("atms_stockmovement"):
            step("movement", "ok", "ใช้ผลรอบตั้งเวลาที่เพิ่งจบ")
        else:
            ok = await _run("atms_stockmovement_light", {"SM_MONTHS": sm_months}, fire_webhook=False)
            step("movement", "ok" if ok else "error", f"{sm_months} เดือน" if ok else "ดึงความเคลื่อนไหวไม่สำเร็จ")

        step("procurement", "running")
        if await _wait_not_running("atms_procurement_light") or await _wait_not_running("atms_procurement"):
            step("procurement", "ok", "ใช้ผลรอบตั้งเวลาที่เพิ่งจบ")
        else:
            # ATMS_PR_PRUNE=0: หน้าต่างสั้นกว่ารอบปกติ ไม่ใช้ลบใบที่หาย ปล่อยให้รอบตั้งเวลาทำตามเกณฑ์เดิม
            ok = await _run(
                "atms_procurement_light",
                {"ATMS_FROM_DATE": from_date, "ATMS_PR_PRUNE": "0"},
                fire_webhook=False,
            )
            step("procurement", "ok" if ok else "error", f"ตั้งแต่ {from_date}" if ok else "ดึง PR/PO/DD ไม่สำเร็จ")

        step("build2", "running")
        ok, info = await wms(build_url)
        step("build2", "ok" if ok else "error", None if ok else info)

        st["status"] = "ok" if all(s["status"] == "ok" for s in st["steps"].values()) else "error"
    except Exception as e:  # noqa: BLE001 - ห้ามหลุดออกไปเงียบๆ ต้องลงสถานะให้หน้าเว็บเห็น
        log.error("wms_live_sync error: %s", e)
        st["status"] = "error"
        st["error"] = str(e)[:300]
        for s in st["steps"].values():
            if s["status"] == "running":
                s["status"] = "error"
                s["finishedAt"] = _live_now()
    finally:
        st["finishedAt"] = _live_now()
        log.info("wms_live_sync %s by %s → %s", inventory, user, st.get("status"))


@router.post("/wms-live-sync", status_code=202)
async def start_wms_live_sync(
    background_tasks: BackgroundTasks,
    body: dict = Body(...),
    _: str = Depends(_verify_key),
):
    inventory = str(body.get("inventory") or "").strip()
    if not inventory.isdigit():
        raise HTTPException(status_code=400, detail="inventory ต้องเป็นรหัสคลัง (ตัวเลข)")
    try:
        days = int(body.get("days") or 14)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="days ต้องเป็นตัวเลข")
    days = max(1, min(days, LIVE_SYNC_MAX_DAYS))
    if _live_sync.get("status") == "running":
        return {"started": False, **_live_sync}
    _live_sync.clear()
    _live_sync.update({
        "status": "running",
        "inventory": inventory,
        "days": days,
        "user": body.get("user"),
        "startedAt": _live_now(),
        "finishedAt": None,
        "error": None,
        "steps": {k: {"status": "pending", "startedAt": None, "finishedAt": None, "note": None} for k in LIVE_SYNC_STEPS},
    })
    background_tasks.add_task(run_wms_live_sync, inventory, days, body.get("user"))
    return {"started": True, **_live_sync}


@router.get("/wms-live-sync")
async def wms_live_sync_status(_: str = Depends(_verify_key)):
    return _live_sync or {"status": "idle"}


@router.post("/finance/overdue-reminders")
async def finance_overdue_reminders(_: str = Depends(_verify_key)):
    """ให้ตัวตั้งเวลาภายนอกสั่งรอบเตือนค้างเคลียร์ · เรียกตัวเดียวกับที่ scheduler ใน main.py เรียก
    (คงรอบ 7 วันกับสวิตช์ FINANCE_EMAIL_ENABLED) · รันนอก event loop"""
    from database import SessionLocal
    from services.finance.overdue_reminder import run_overdue_reminders

    try:
        result = await asyncio.get_running_loop().run_in_executor(None, run_overdue_reminders, SessionLocal)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc) or exc.__class__.__name__)
    return {"ok": True, "result": jsonable_encoder(result)}
