"""ATMS — ดึง "ยอดรวมสุทธิ" ของใบ DD สดจากหน้า ATMS ตามคำขอ (on-demand)

  POST /atms/deposit/refresh   {"codes": ["KKDD26090025", ...]}  → ยอดสุทธิล่าสุดรายใบ

ทำไมต้องมี: `deposit_header.amount_net` ถูกเติมโดยไปป์ไลน์รายคืน/ราย 4 ชม. เท่านั้น ใบที่ถูก
**แก้ VAT ใน ATMS หลังเราดึงไปแล้ว** จะค้างยอดเดิมจนรอบถัดไปมาทาน (เคสจริง KKDD26090025 —
เก็บ 13/10 ได้ 0% / 3,700 ต่อมามีคนแก้เป็น 7% ยอดจริง 3,959 แต่ /ap-tracking โชว์ 3,700 อยู่ 6 วัน
จนผู้ใช้แจ้งเข้ามา 19/10) เส้นนี้ให้ mena-wms ดึงสด ณ วินาทีที่ผู้ใช้เปิดใบ/กดส่งบัญชี/กดจ่าย
ยอดที่คนตัดสินใจเรื่องเงินเห็น จึงเป็นยอดของ ATMS เสมอ ไม่ใช่ยอดที่เราแคชไว้

**ห้ามคำนวณ VAT เอง** — อัตราภาษีเป็นของแต่ละใบ DD ไม่ใช่ของ PO และไม่ใช่ของผู้ขาย
(ผู้ขายรายเดียวกันออกได้ทั้ง 7% และ 0%) อ่านจากหน้าใบเท่านั้น ดู parse_deposit_totals

ขอบเขตที่ตั้งใจให้แคบ:
  · ≤ MAX_CODES ใบต่อคำขอ — ฝั่งเว็บต้องส่งเฉพาะใบที่ผู้ใช้เห็น/แตะ ไม่ใช่ทั้งเดือน
  · coalescing: ใบที่เพิ่งดึงไม่ถึง fresh_sec วินาที ตอบค่าในฐาน ไม่ยิง ATMS ซ้ำ
    (หลายคนเปิดหน้าเดียวกันพร้อมกัน = ยิง ATMS ชุดเดียว) · force=true ข้ามด่านนี้ ใช้ที่จุดจ่ายเงิน
  · เพดาน in-flight ทั้งโปรเซส MAX_INFLIGHT — ATMS login เดียวทั้งระบบ ถ้าปล่อยให้ทุก request
    ยิงพร้อมกันตามใจ หน้าเว็บ ATMS ของคนอื่นจะอืดไปด้วย (เคยทำทรุดมาแล้วตอน 176 requests รวดเดียว)
"""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache
from typing import Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from pymongo import MongoClient

from scripts.atms_procurement.pipeline_atms_procurement import (
    BASE,
    get_session,
    parse_deposit_totals,
)

router = APIRouter(prefix="/atms/deposit", tags=["ATMS Deposit Totals"])

MAX_CODES = 100          # ต่อคำขอ — มากกว่านี้ให้ฝั่งเว็บแบ่งก้อนมาเอง
MAX_WORKERS = 12         # วัดจริง 19/10/2026: 12 เธรด = ~50 ใบ/วิ ไม่เจอ 500 เลย
MAX_INFLIGHT = 12        # เพดานรวมทั้งโปรเซส (ไม่ใช่ต่อคำขอ)
FRESH_SEC = 60           # ใบที่ดึงไปไม่ถึงเท่านี้ ถือว่าสดพอ ไม่ยิงซ้ำ
TOTALS_FIELDS = ("grand_total", "vat_rate", "vat_amount", "amount_net")

_inflight = threading.Semaphore(MAX_INFLIGHT)
_session: Optional[requests.Session] = None
_session_lock = threading.Lock()


def _verify_key(x_api_key: str = Header(..., alias="x-api-key")):
    key = os.getenv("PIPELINE_API_KEY")
    if not key or x_api_key != key:
        raise HTTPException(status_code=401, detail="Unauthorized")


@lru_cache(maxsize=1)
def _db():
    # scripts ใช้ MONGODB_URI · services/mongo_service ใช้ MONGO_URI — รับทั้งสองชื่อ
    uri = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
    if not uri:
        raise HTTPException(status_code=500, detail="MONGODB_URI not set")
    return MongoClient(uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)["atms"]


def _get_session(force_new: bool = False) -> requests.Session:
    """session ATMS ตัวเดียวทั้งโปรเซส — login ใหม่เฉพาะตอนหลุด (ดู _fetch)"""
    global _session
    with _session_lock:
        if force_new or _session is None:
            _session = get_session()
        return _session


class RefreshRequest(BaseModel):
    codes: List[str] = Field(default_factory=list, description="เลขที่ใบ DD เช่น KKDD26090025")
    force: bool = Field(False, description="true = ดึงสดทุกใบ ไม่สน fresh_sec (ใช้ที่จุดจ่ายเงิน)")
    fresh_sec: int = Field(FRESH_SEC, ge=0, le=3600, description="ใบที่ดึงไปไม่ถึงกี่วินาที ถือว่าสดพอ")


def _age_sec(at) -> float:
    """อายุของ totals_at เป็นวินาที — ไม่มี/อ่านไม่ได้ = เก่าอนันต์"""
    if not isinstance(at, datetime):
        return float("inf")
    at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - at).total_seconds()


def _row(h: dict, source: str, changed: bool = False) -> dict:
    return {
        "deposit_code": h.get("deposit_code"),
        "amount": h.get("amount"),
        "amount_net": h.get("amount_net"),
        "grand_total": h.get("grand_total"),
        "vat_rate": h.get("vat_rate"),
        "vat_amount": h.get("vat_amount"),
        "totals_at": h.get("totals_at").isoformat() if isinstance(h.get("totals_at"), datetime) else None,
        "totals_missing": bool(h.get("totals_missing")),
        "source": source,          # atms = ดึงสดรอบนี้ · cache = ยังสดพอ · error = ยิงไม่สำเร็จ
        "changed": changed,        # ยอดสุทธิต่างจากที่เก็บไว้ก่อนรอบนี้
    }


def _fetch(h: dict) -> dict:
    """เปิดหน้าใบ DD แล้ว parse ยอด — ลองใหม่ 1 รอบพร้อม login ใหม่ถ้า session หลุด"""
    for attempt in (0, 1):
        try:
            s = _get_session(force_new=attempt == 1)
            r = s.get(f"{BASE}/inv/deposit/view/id/{h['deposit_id']}", timeout=45)
            if "account/user/login" in r.url:
                continue                        # session หมดอายุ — รอบถัดไป login ใหม่
            r.raise_for_status()
            return parse_deposit_totals(r.text)
        except requests.RequestException:
            continue
    return {}


@router.post("/refresh", dependencies=[Depends(_verify_key)])
def refresh_totals(body: RefreshRequest):
    """ดึงยอดรวมสุทธิของใบ DD ที่ระบุให้สด แล้วคืนค่าล่าสุดรายใบ"""
    codes = [c.strip() for c in body.codes if c and c.strip()]
    codes = list(dict.fromkeys(codes))                      # กันเลขซ้ำในคำขอเดียว
    if not codes:
        return {"rows": [], "fetched": 0, "changed": 0, "errors": 0}
    if len(codes) > MAX_CODES:
        raise HTTPException(status_code=400,
                            detail=f"ครั้งละไม่เกิน {MAX_CODES} ใบ (ส่งมา {len(codes)} ใบ)")

    col = _db()["deposit_header"]
    heads = {str(h["deposit_code"]): h for h in col.find(
        {"deposit_code": {"$in": codes}},
        {"_id": 0, "deposit_id": 1, "deposit_code": 1, "amount": 1, "amount_net": 1,
         "grand_total": 1, "vat_rate": 1, "vat_amount": 1, "totals_at": 1, "totals_missing": 1})}

    rows: Dict[str, dict] = {}
    todo: List[dict] = []
    for c in codes:
        h = heads.get(c)
        if not h:
            rows[c] = {"deposit_code": c, "source": "not_found", "changed": False}
        elif not body.force and _age_sec(h.get("totals_at")) < body.fresh_sec:
            rows[c] = _row(h, "cache")                      # สดพออยู่แล้ว ไม่ต้องแตะ ATMS
        elif not h.get("deposit_id"):
            rows[c] = _row(h, "error")                      # ไม่มี id = เปิดหน้า detail ไม่ได้
        else:
            todo.append(h)

    fetched = changed = errors = 0
    if todo:
        with _inflight:
            workers = min(MAX_WORKERS, len(todo))
            with ThreadPoolExecutor(max_workers=workers) as ex:
                results = list(ex.map(lambda h: (h, _fetch(h)), todo))
        now = datetime.now(timezone.utc)
        for h, totals in results:
            code = str(h["deposit_code"])
            if not totals:
                # ใบเปล่ายอด 0.00 ไม่มีตารางในหน้า DD จริง ๆ (ใบที่ถูกยกเลิกด้วย) — ลงเครื่องหมาย
                # ไว้ให้เลิกไล่ดึงซ้ำ · ใบที่ยอดไม่เป็น 0 ถือว่ายิงพลาด ปล่อยให้รอบหน้าลองใหม่
                if str(h.get("amount") or "0").replace(",", "").strip() in ("", "0", "0.00"):
                    col.update_one({"deposit_id": h["deposit_id"]},
                                   {"$set": {"totals_missing": True, "totals_at": now}})
                    rows[code] = _row({**h, "totals_missing": True, "totals_at": now}, "atms")
                else:
                    errors += 1
                    rows[code] = _row(h, "error")
                continue
            fetched += 1
            was = str(h.get("amount_net") or "")
            is_changed = was != str(totals.get("amount_net") or "")
            if is_changed:
                changed += 1
            col.update_one({"deposit_id": h["deposit_id"]},
                           {"$set": {**totals, "totals_at": now},
                            "$unset": {"totals_missing": ""}})
            rows[code] = _row({**h, **totals, "totals_at": now, "totals_missing": False},
                              "atms", is_changed)

    return {"rows": [rows[c] for c in codes], "fetched": fetched,
            "changed": changed, "errors": errors}
