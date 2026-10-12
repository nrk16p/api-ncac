"""master รถจาก MongoDB (อ่านอย่างเดียว)

api-ncac ยังไม่เคยต่อ Mongo ในชั้น route มาก่อน (เดิมมีแต่ใน scripts/) — ที่นี่จึงสร้าง client
ครั้งเดียวแบบ lazy พร้อม timeout สั้น และทุก query มี filter + limit + maxTimeMS
cluster เล็ก (cache ~370MB) ห้ามสแกนทั้ง collection
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)

_TIMEOUT_MS = 3000
_MAX_TIME_MS = 2000
_MIN_QUERY = 2

PROJECTION = {"_id": 0, "เลขรถ": 1, "ทะเบียน": 1, "ประเภทรถ": 1}


@lru_cache(maxsize=1)
def _collection():
    from pymongo import MongoClient
    uri = os.getenv("MONGODB_URI")
    if not uri:
        raise RuntimeError("ไม่ได้ตั้งค่า MONGODB_URI")
    client = MongoClient(uri, serverSelectionTimeoutMS=_TIMEOUT_MS,
                         connectTimeoutMS=_TIMEOUT_MS, socketTimeoutMS=_TIMEOUT_MS)
    return client["atms"]["truck_master_monthly"]


def search_trucks(query: str, limit: int = 20) -> list[dict]:
    """ค้นได้ทั้งรหัสรถภายใน (161, F014) และทะเบียน (71-4285) ตามที่ใบจริงเขียนสองแบบ

    Mongo ล่มหรือช้าไม่ทำให้ฟอร์มใช้ไม่ได้ — คืนลิสต์ว่างแล้ว log ไว้
    """
    q = (query or "").strip()
    if len(q) < _MIN_QUERY:
        return []
    pattern = {"$regex": q, "$options": "i"}
    try:
        rows = list(_collection()
                    .find({"$or": [{"เลขรถ": pattern}, {"ทะเบียน": pattern}]}, PROJECTION)
                    .limit(limit)
                    .max_time_ms(_MAX_TIME_MS))
    except Exception as exc:
        logger.warning("truck master lookup failed for %r: %s", q, exc)
        return []

    seen, out = set(), []
    for row in rows:
        code = str(row.get("เลขรถ") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append({"truck_code": code,
                    "truck_plate": str(row.get("ทะเบียน") or "").strip(),
                    "truck_type": str(row.get("ประเภทรถ") or "").strip()})
    return out


def truck_by_code(code: str) -> Optional[dict]:
    wanted = (code or "").strip()
    return next((t for t in search_trucks(wanted, limit=20) if t["truck_code"] == wanted), None)
