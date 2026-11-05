"""store-withdraw-drift — ปลุก WMS ให้ตรวจใบเบิกอะไหล่ที่ตัดแล้วว่าถูกแก้ใน ATMS หรือไม่.

POST {WMS_BASE}/api/store-withdraw/drift-tick  (header x-api-key = WMS_STORE_WITHDRAW_KEY)
WMS เป็นคนอ่าน ATMS และเขียน drift ลงใบเอง สคริปต์นี้แค่ยิงแล้ว log ผล {checked, drifted, skipped}.
Env (ใช้ค่าเดิมเป็นค่าเริ่มต้น): WMS_BASE (ไม่ตั้ง = https://mena-wms.vercel.app),
WMS_STORE_WITHDRAW_KEY (ไม่ตั้ง = PIPELINE_API_KEY). ตั้งรอบทุก 30 นาที 07:00-20:00 (BKK)
เป็นงานบน Render cron ที่เรียก POST /pipeline/run/store-withdraw-drift
Env ต้องตรงกัน: กุญแจที่ส่ง (WMS_STORE_WITHDRAW_KEY หรือ PIPELINE_API_KEY) ต้องเท่ากับกุญแจ tick ของ WMS
(STORE_WITHDRAW_TICK_KEY หรือ NCAC_API_KEY ซึ่งปกติ = PIPELINE_API_KEY อยู่แล้ว) ·
WMS_BASE = URL ของ WMS · รัน uvicorn แบบ worker เดียว (ล็อกต่อผู้ใช้อยู่ใน process)
"""
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engineon"))
from common import JobLog, log  # noqa: E402

DEFAULT_WMS_BASE = "https://mena-wms.vercel.app"
TIMEOUT_S = 75  # WMS หยุดเองก่อน ~50 วิ (ข้อจำกัด Vercel) เผื่อ cold start


def tick(base: str, key: str, post=requests.post) -> dict:
    """ยิง drift-tick หนึ่งรอบ · คืน JSON ของ WMS · ไม่ใช่ 2xx = raise"""
    url = base.rstrip("/") + "/api/store-withdraw/drift-tick"
    r = post(url, headers={"x-api-key": key}, timeout=TIMEOUT_S)
    if not 200 <= r.status_code < 300:
        raise RuntimeError(f"WMS drift-tick ตอบ {r.status_code}: {r.text[:200]}")
    return r.json()


def main() -> int:
    job = JobLog("store_withdraw_drift", "store-withdraw-drift")
    base = os.getenv("WMS_BASE") or DEFAULT_WMS_BASE
    key = os.getenv("WMS_STORE_WITHDRAW_KEY") or os.getenv("PIPELINE_API_KEY")
    if not key:
        msg = "ต้องตั้ง WMS_STORE_WITHDRAW_KEY หรือ PIPELINE_API_KEY"
        log.error("store-withdraw-drift: %s", msg)
        job.finish("failed", error=msg)
        return 1
    try:
        res = tick(base, key)
    except Exception as e:  # noqa: BLE001 — log แล้วปิดงานเป็น failed
        log.error("store-withdraw-drift failed: %s", e)
        job.finish("failed", error=str(e)[:500])
        return 1
    log.info("store-withdraw-drift: %s", res)
    job.finish("success", **{k: res.get(k) for k in ("checked", "drifted", "skipped")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
