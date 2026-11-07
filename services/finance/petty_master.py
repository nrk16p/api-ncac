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


# ---------------------------------------------------------------------------
# master คนขับ + ฟลีท ณ วันทำรายการ จาก atms.vehicle_daily_transaction (รายงานรถประจำวัน ATMS หนึ่งแถว = รถหนึ่งคันในหนึ่งวัน)
# ผู้ใช้สั่ง 7/11/2026 ข้อ 7 (ค้นชื่อคนขับ แนะนำ Mixer L/S และ พจส/พจร) และข้อ 10 (ฟลีทจากทะเบียน/เบอร์รถ/คนขับ ณ วันที่ทำ)
# คอลัมน์ที่ใช้: คนขับ_รหัส คนขับ_ชื่อ คนขับ_สถานะ (พจส/พจร/พจส.สแปร์/สนง) เบอร์รถ ทะเบียน plate_norm fleet_group_name ฟลีท date
# index ที่มี: date · plate_norm+date · คนขับ_รหัส+date · fleet_group_id+year_month — ค้นชื่อเป็น regex จึงบีบด้วยช่วงวันก่อนเสมอ
# ---------------------------------------------------------------------------

_DAILY_PROJECTION = {"_id": 0, "date": 1, "เบอร์รถ": 1, "ทะเบียน": 1, "plate_norm": 1, "fleet_group_name": 1, "ฟลีท": 1,
                     "คนขับ_รหัส": 1, "คนขับ_ชื่อ": 1, "คนขับ_สถานะ": 1}
_LOOKBACK_DAYS = 45  # รถ/คนขับที่ไม่วิ่งมานานกว่านี้ถือว่าไม่รู้ฟลีท

# กลุ่มฟลีทของ ATMS → ชื่อฟลีทที่บัญชีใช้ (ผู้ใช้ให้ 4 ชื่อ: Mixer L / Mixer S / Trailer / Side curtain)
# โม่ใหญ่/โม่เล็ก ชัดจากข้อมูล 7/11/2026 · SCCC (ปูนผง) = Trailer · BTG/TFG (อาหารสัตว์) = Side curtain = ที่ผมตีความ รอผู้ใช้ยืนยัน
FLEET_BY_GROUP = {"โม่ใหญ่": "Mixer L", "โม่เล็ก": "Mixer S", "SCCC": "Trailer", "BTG": "Side curtain", "TFG": "Side curtain"}
TRUCK_TYPE_BY_FLEET = {"Mixer L": "โม่ใหญ่", "Mixer S": "โม่เล็ก"}


def _daily_collection():
    from pymongo import MongoClient
    uri = os.getenv("MONGODB_URI")
    if not uri:
        raise RuntimeError("ไม่ได้ตั้งค่า MONGODB_URI")
    client = MongoClient(uri, serverSelectionTimeoutMS=_TIMEOUT_MS,
                         connectTimeoutMS=_TIMEOUT_MS, socketTimeoutMS=_TIMEOUT_MS)
    return client["atms"]["vehicle_daily_transaction"]


def fleet_name(group) -> str:
    g = str(group or "").strip()
    return FLEET_BY_GROUP.get(g, g)


def driver_role(status) -> str:
    """'พจส' / 'พจส.' / 'พจส.สแปร์' → 'พจส.' · 'พจร' → 'พจร.' · อื่น ๆ (สนง ว่าง) → ''"""
    s = str(status or "").strip()
    if s.startswith("พจร"):
        return "พจร."
    if s.startswith("พจส"):
        return "พจส."
    return ""


def norm_plate(plate) -> str:
    """'สบ.71-4372' / '71 4372' → '71-4372' แบบเดียวกับ plate_norm ของ pipeline (ตัดจังหวัด ตัดช่องว่าง)"""
    text = str(plate or "").replace("สบ.", "")
    text = re.sub(r"\s+", "", text)
    return text


def _window(on_date):
    from datetime import date, datetime, timedelta
    if isinstance(on_date, str) and on_date:
        on_date = date.fromisoformat(on_date[:10])
    end = on_date or date.today()
    start = end - timedelta(days=_LOOKBACK_DAYS)
    to_dt = lambda d: datetime(d.year, d.month, d.day)  # noqa: E731
    return to_dt(start), to_dt(end) + timedelta(days=1)


def _row_out(row: dict) -> dict:
    fleet = fleet_name(row.get("fleet_group_name"))
    d = row.get("date")
    return {
        "driver_code": str(row.get("คนขับ_รหัส") or "").strip() or None,
        "driver_name": str(row.get("คนขับ_ชื่อ") or "").strip() or None,
        "driver_role": driver_role(row.get("คนขับ_สถานะ")),
        "fleet": fleet,
        "truck_code": str(row.get("เบอร์รถ") or "").strip() or None,
        "truck_plate": str(row.get("plate_norm") or norm_plate(row.get("ทะเบียน"))).strip() or None,
        "truck_type": TRUCK_TYPE_BY_FLEET.get(fleet, ""),
        "customer_fleet": str(row.get("ฟลีท") or "").strip() or None,
        "date": d.date().isoformat() if hasattr(d, "date") else (str(d)[:10] if d else None),
    }


def search_drivers(query: str, on_date=None, limit: int = 20) -> list[dict]:
    """ค้นคนขับจากชื่อหรือรหัส ในช่วง 45 วันก่อนวันที่ทำรายการ คืนแถวล่าสุดคนละหนึ่ง (ฟลีท/รถ/ประเภทคนขับ ณ วันนั้น)
    Mongo ล่มหรือช้าคืนลิสต์ว่าง ไม่ทำให้ฟอร์มพัง"""
    q = (query or "").strip()
    if len(q) < _MIN_QUERY:
        return []
    start, end = _window(on_date)
    pattern = {"$regex": re.escape(q), "$options": "i"}
    try:
        rows = list(_daily_collection()
                    .find({"date": {"$gte": start, "$lt": end},
                           "$or": [{"คนขับ_ชื่อ": pattern}, {"คนขับ_รหัส": pattern}]}, _DAILY_PROJECTION)
                    .sort("date", -1).limit(limit * 60)  # คนละไม่เกิน ~45 วัน
                    .max_time_ms(_MAX_TIME_MS))
    except Exception as exc:
        logger.warning("driver master lookup failed for %r: %s", q, exc)
        return []
    newest: dict[str, dict] = {}
    for row in rows:  # เรียงวันล่าสุดมาก่อนแล้ว แถวแรกของแต่ละคน = ล่าสุด
        key = str(row.get("คนขับ_รหัส") or row.get("คนขับ_ชื่อ") or "").strip()
        if key and key not in newest:
            newest[key] = row
    out = [_row_out(r) for r in newest.values()]
    out.sort(key=lambda r: (0 if (r["driver_name"] or "").startswith(q) or (r["driver_code"] or "") == q else 1,
                            r["driver_name"] or ""))
    return out[:limit]


def fleet_at(truck_code=None, truck_plate=None, driver_code=None, on_date=None) -> Optional[dict]:
    """ฟลีท (และคนขับ) ของรถคันนี้ในวันที่ทำรายการ: แถวของวันนั้น ไม่มีใช้แถวล่าสุดภายใน 45 วันก่อนหน้า
    ค้นจากทะเบียน (index plate_norm) ก่อน ไม่มีค่อยเบอร์รถ หรือรหัสคนขับ · ไม่พบ/Mongo ล่ม = None"""
    start, end = _window(on_date)
    clauses = []
    plate = norm_plate(truck_plate)
    if plate:
        clauses.append({"plate_norm": plate})
    code = str(truck_code or "").strip()
    if code:
        clauses.append({"เบอร์รถ": {"$regex": f"^{re.escape(code)}$", "$options": "i"}})
    drv = str(driver_code or "").strip()
    if drv:
        clauses.append({"คนขับ_รหัส": drv})
    if not clauses:
        return None
    try:
        row = (_daily_collection()
               .find({"date": {"$gte": start, "$lt": end}, "$or": clauses}, _DAILY_PROJECTION)
               .sort("date", -1).limit(1).max_time_ms(_MAX_TIME_MS))
        row = next(iter(row), None)
    except Exception as exc:
        logger.warning("fleet lookup failed for %r/%r: %s", truck_code, truck_plate, exc)
        return None
    return _row_out(row) if row else None
