"""ATMS — อ่านทะเบียนสินค้า (SKU) รายรหัสสดจากหน้า ATMS ตามคำขอ

  GET /atms/sku/lookup?codes=04-11,1-2345   → ข้อมูลรหัสสินค้าล่าสุดรายรหัส

ทำไมต้องมี: mena-wms หน้า /sku/new ต้องให้ผู้ใช้เลือก "รหัสสินค้า ATMS" แล้วเติมชื่อ/กลุ่ม/
ยี่ห้อ/หน่วย/คลัง ให้อัตโนมัติ · ฝั่ง wms มีสำเนาทะเบียนสินค้า (atms_sku_master) ที่ซิงก์คืนละครั้ง
รหัสที่คลังเพิ่งเปิดในวันนั้น หรือรหัสที่มีคนแก้ชื่อ/หน่วยวันนี้ จะยังไม่มี/ยังค้างค่าเดิมจนถึงรอบถัดไป
คนกรอกจึงได้ข้อมูลผิดหรือหาไม่เจอ · เส้นนี้อ่านจากหน้า ATMS ณ วินาทีที่ผู้ใช้เลือกรหัส

ทำไมอยู่ที่ api-ncac ไม่ใช่ที่ mena-wms: ที่นี่มี ATMS_USERNAME/ATMS_PASSWORD แล้ว login ใหม่เองได้
เมื่อ session หลุด (ดู get_session) ส่วน mena-wms ถือแค่คุกกี้ PHPSESSID ที่ฝังไว้ในโค้ด/env
ซึ่งหมดอายุเงียบ ๆ (ตรวจ 19/10/2026: ค่า fallback ในรีโป wms หมดอายุไปแล้ว)

ขอบเขตที่ตั้งใจให้แคบ — ATMS login เดียวทั้งระบบ ยิงรัวทำหน้าเว็บคนอื่นอืด (เคยทรุดมาแล้ว):
  · ≤ MAX_CODES รหัสต่อคำขอ (หน้า /sku/new เลือกทีละรหัส)
  · เพดาน in-flight ทั้งโปรเซส MAX_INFLIGHT
  · ไม่รับคำค้นด้วย "ชื่อ" — การค้นหาตอนพิมพ์ให้ wms อ่านจากสำเนาในฐานของตัวเอง
    (พิมพ์ทีละตัวอักษรแล้วยิง ATMS ทุกครั้ง = สิ่งที่ทำให้ ATMS ทรุด)
"""
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from scripts.atms_procurement.pipeline_atms_procurement import BASE, get_session

router = APIRouter(prefix="/atms/sku", tags=["ATMS SKU Lookup"])

MAX_CODES = 20
MAX_WORKERS = 5
MAX_INFLIGHT = 8

_inflight = threading.Semaphore(MAX_INFLIGHT)
_session: Optional[requests.Session] = None
_session_lock = threading.Lock()

# พารามิเตอร์ชุดเดียวกับฟอร์มค้นหาของหน้าทะเบียนสินค้า — ส่งไม่ครบ ATMS คืนรายการทั้งคลัง
_SEARCH_PARAMS = {
    "name": "", "remark": "", "type": "", "inventory_id": "", "sku_tag_id": "",
    "stock_unit_id": "", "brand_id": "", "is_tire": "", "trackable": "",
    "has_serial_no": "", "no_gl_code": "0", "submit": "ค้นหา", "order_by": "s.code asc",
}

_TD = re.compile(r"<td[^>]*>([\s\S]*?)</td>")
_TR = re.compile(r"<tr[^>]*>([\s\S]*?)</tr>")
_TBODY = re.compile(r"<tbody[\s\S]*?</tbody>")
_PK = re.compile(r"/inv/sku/view/id/(\d+)")
_TAG = re.compile(r"<[^>]*>")


def _verify_key(x_api_key: str = Header(..., alias="x-api-key")):
    key = os.getenv("PIPELINE_API_KEY")
    if not key or x_api_key != key:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _get_session(force_new: bool = False) -> requests.Session:
    """session ATMS ตัวเดียวทั้งโปรเซส — login ใหม่เฉพาะตอนหลุด (ดู _fetch)"""
    global _session
    with _session_lock:
        if force_new or _session is None:
            _session = get_session()
        return _session


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub("", html)).strip()


def _num(s: str) -> float:
    try:
        return float(s.replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _parse_row(tr_inner: str) -> Optional[dict]:
    """แกะหนึ่งแถวของตารางทะเบียนสินค้า — ตำแหน่งคอลัมน์ตรงกับ lib/atms-sku-log.ts ของ mena-wms
    คืน None เมื่อแถวไม่ครบคอลัมน์หรือหา pk ไม่เจอ (แถวหัว/แถวสรุป)"""
    raw = _TD.findall(tr_inner)
    if len(raw) < 15:
        return None
    pk = _PK.search(raw[14])
    if not pk:
        return None
    tds = [_text(c) for c in raw]
    return {
        "sku_pk": int(pk.group(1)),
        "code": tds[0],
        "name": tds[1],
        "group": tds[2],
        "warehouse": tds[3],
        "oracle_code": tds[4],
        "brand": tds[5],
        "unit": tds[9],
        "stock_qty": _num(tds[6]),
        "min_qty": _num(tds[7]),
        "max_qty": _num(tds[8]),
    }


def _fetch(code: str) -> dict:
    """ค้นรหัสในหน้าทะเบียนสินค้าแล้วคืนแถวที่รหัสตรงกันเป๊ะ (ATMS ค้นแบบ 'ขึ้นต้นด้วย')
    ลองใหม่ 1 รอบพร้อม login ใหม่ถ้า session หลุด"""
    for attempt in (0, 1):
        try:
            s = _get_session(force_new=attempt == 1)
            r = s.get(f"{BASE}/inv/sku/index", params={**_SEARCH_PARAMS, "code": code}, timeout=45)
            if "account/user/login" in r.url:
                continue                        # session หมดอายุ — รอบถัดไป login ใหม่
            r.raise_for_status()
            body = _TBODY.search(r.text)
            if not body:
                return {"code": code, "found": False, "source": "atms"}
            # รหัสเดียวกันมีได้หลายคลัง — คืนแถวที่มีของในสต๊อกก่อน ไม่มีเลยเอาแถวแรก
            hits = [row for tr in _TR.findall(body.group(0))
                    if (row := _parse_row(tr)) and row["code"].lower() == code.lower()]
            if not hits:
                return {"code": code, "found": False, "source": "atms"}
            hits.sort(key=lambda h: (-h["stock_qty"], h["sku_pk"]))
            return {**hits[0], "found": True, "source": "atms",
                    "warehouses": [h["warehouse"] for h in hits]}
        except requests.RequestException:
            continue
    return {"code": code, "found": False, "source": "error"}


@router.get("/lookup", dependencies=[Depends(_verify_key)])
def lookup(codes: str = Query(..., description="รหัสสินค้า ATMS คั่นด้วย comma เช่น 04-11,1-2345")):
    """อ่านทะเบียนสินค้ารายรหัสสดจาก ATMS"""
    wanted = [c.strip() for c in codes.split(",") if c.strip()]
    wanted = list(dict.fromkeys(wanted))                    # กันรหัสซ้ำในคำขอเดียว
    if not wanted:
        return {"rows": [], "found": 0, "errors": 0}
    if len(wanted) > MAX_CODES:
        raise HTTPException(status_code=400,
                            detail=f"ครั้งละไม่เกิน {MAX_CODES} รหัส (ส่งมา {len(wanted)} รหัส)")

    with _inflight:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(wanted))) as ex:
            rows: List[dict] = list(ex.map(_fetch, wanted))

    by_code: Dict[str, dict] = {r["code"].lower(): r for r in rows}
    ordered = [by_code.get(c.lower(), {"code": c, "found": False, "source": "error"}) for c in wanted]
    return {"rows": ordered,
            "found": sum(1 for r in ordered if r.get("found")),
            "errors": sum(1 for r in ordered if r.get("source") == "error")}
