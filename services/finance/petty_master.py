"""master รถจาก MongoDB (อ่านอย่างเดียว)

api-ncac ยังไม่เคยต่อ Mongo ในชั้น route มาก่อน (เดิมมีแต่ใน scripts/) — ที่นี่จึงสร้าง client
ครั้งเดียวแบบ lazy พร้อม timeout สั้น และทุก query มี filter + limit + maxTimeMS
cluster เล็ก (cache ~370MB) ห้ามสแกนทั้ง collection
"""
from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)

_TIMEOUT_MS = 3000
_MAX_TIME_MS = 2000
_MIN_QUERY = 2

PROJECTION = {"_id": 0, "truck_no": 1, "plate": 1, "customer": 1, "month_year": 1}

# customer ลงท้าย " L" = โม่ใหญ่, " S" = โม่เล็ก (ดูจาก Mongo 2026-10-13: "Cpac L", "Scco S") · อื่น ๆ เว้นว่าง
_TYPE_BY_SUFFIX = {"L": "โม่ใหญ่", "S": "โม่เล็ก"}


@lru_cache(maxsize=1)
def _collection():
    from pymongo import MongoClient
    uri = os.getenv("MONGODB_URI")
    if not uri:
        raise RuntimeError("ไม่ได้ตั้งค่า MONGODB_URI")
    client = MongoClient(uri, serverSelectionTimeoutMS=_TIMEOUT_MS,
                         connectTimeoutMS=_TIMEOUT_MS, socketTimeoutMS=_TIMEOUT_MS)
    return client["atms"]["truck_master_monthly"]


def _truck_type(customer) -> str:
    parts = str(customer or "").strip().split()
    return _TYPE_BY_SUFFIX.get(parts[-1], "") if parts else ""


def _month_key(month_year) -> tuple[int, int]:
    """"12-67" → (67, 12) เรียงเป็นตัวเลข ไม่ใช่ตัวอักษร (ไม่งั้น "9-68" จะชนะ "12-68")"""
    try:
        month, year = str(month_year or "").split("-", 1)
        return (int(year), int(month))
    except Exception:
        return (0, 0)


# ตัวคั่นในเบอร์รถ/ทะเบียนที่คนพิมพ์บ้างไม่พิมพ์บ้าง ("สบ.71-1325" · "71 1325" · "711325")
SEPARATORS = r"[\s.\-]*"
_SEP_CHARS = re.compile(r"[\s.\-]+")


def _squash(text) -> str:
    return _SEP_CHARS.sub("", str(text or "")).upper()


def search_pattern(query: str) -> str:
    """regex ค้นส่วนไหนของเบอร์รถ/ทะเบียนก็ได้ โดยไม่สนตัวคั่น · ทุกตัวอักษร escape ไว้ (พิมพ์ .* ก็เป็นตัวอักษร)"""
    return SEPARATORS.join(re.escape(ch) for ch in _squash(query))


def match_rank(query: str, code, plate) -> Optional[int]:
    """ยิ่งน้อยยิ่งตรง: 0 เบอร์รถตรงเป๊ะ · 1 เบอร์รถขึ้นต้น/ลงท้ายด้วยคำค้น · 2 อยู่กลางเบอร์รถ ·
    3 ทะเบียนลงท้ายด้วยคำค้น · 4 อยู่ในทะเบียน · None = ไม่ตรง"""
    q, c, pl = _squash(query), _squash(code), _squash(plate)
    if not q:
        return None
    if c == q:
        return 0
    if c.startswith(q) or c.endswith(q):
        return 1
    if q in c:
        return 2
    if pl.endswith(q):
        return 3
    if q in pl:
        return 4
    return None


def search_trucks(query: str, limit: int = 20) -> list[dict]:
    """ค้นได้ทั้งเบอร์รถ (พิมพ์ 1093 เจอ TH1093) และทะเบียน · รถหนึ่งคันมีแถวรายเดือน คืนแถวล่าสุดคันละหนึ่ง

    Mongo ล่มหรือช้าไม่ทำให้ฟอร์มใช้ไม่ได้ — คืนลิสต์ว่างแล้ว log ไว้
    """
    q = (query or "").strip()
    if len(q) < _MIN_QUERY:
        return []
    pattern = {"$regex": search_pattern(q), "$options": "i"}
    try:
        rows = list(_collection()
                    .find({"$or": [{"truck_no": pattern}, {"plate": pattern}]}, PROJECTION)
                    .limit(limit * 24)  # รถละไม่เกิน ~24 เดือน
                    .max_time_ms(_MAX_TIME_MS))
    except Exception as exc:
        logger.warning("truck master lookup failed for %r: %s", q, exc)
        return []

    newest: dict[str, dict] = {}
    for row in rows:
        code = str(row.get("truck_no") or "").strip()
        if not code:
            continue
        if code not in newest or _month_key(row.get("month_year")) > _month_key(newest[code].get("month_year")):
            newest[code] = row
    out = [{"truck_code": code,
            "truck_plate": str(row.get("plate") or "").strip(),
            "truck_type": _truck_type(row.get("customer"))}
           for code, row in newest.items()]
    # ตรงที่สุดขึ้นก่อน (เบอร์รถตรงเป๊ะ → ขึ้นต้น/ลงท้าย → อยู่ในทะเบียน) แล้วเรียงตามเบอร์รถ
    def _order(t: dict) -> tuple[int, str]:
        rank = match_rank(q, t["truck_code"], t["truck_plate"])
        return (9 if rank is None else rank, t["truck_code"])

    out.sort(key=_order)
    return out[:limit]


def truck_by_code(code: str) -> Optional[dict]:
    wanted = (code or "").strip()
    return next((t for t in search_trucks(wanted, limit=20) if t["truck_code"] == wanted), None)
