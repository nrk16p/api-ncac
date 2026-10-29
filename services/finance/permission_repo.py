"""โหลดสิทธิ์ตามหน้าที่จาก fin_permissions พร้อมแคชในหน่วยความจำ

สิทธิ์เปลี่ยนไม่บ่อย จึงแคช 60 วินาทีแบบเดียวกับ approval_repo
การตัดสินว่า "กดได้ไหม" ใช้ load_grants(db, fresh=True) เสมอ เพื่อไม่ให้สิทธิ์ที่เพิ่งถูกถอดยังใช้ได้
"""
from __future__ import annotations

import threading
import time

from models.finance_model import FinPermission

GRANTS_TTL_SECONDS = 60
_clock = time.monotonic
_lock = threading.Lock()
_cache = None  # (expires_at, grants)


def clear_grants_cache():
    global _cache
    with _lock:
        _cache = None


def _load(db) -> dict:
    grants: dict = {}
    for row in db.query(FinPermission).all():
        grants.setdefault(row.permission, set()).add(row.employee_id)
    return grants


def load_grants(db, fresh=False) -> dict:
    """{permission: {employee_id, ...}} — อ่านอย่างเดียว ห้ามแก้ของที่คืนมา"""
    global _cache
    if not fresh:
        with _lock:
            hit = _cache
        if hit is not None and _clock() < hit[0]:
            return hit[1]
    grants = _load(db)
    with _lock:
        _cache = (_clock() + GRANTS_TTL_SECONDS, grants)
    return grants
