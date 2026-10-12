"""อ่านใบรับสินค้า (DD) จาก atms.stockmovement_v5 มาจัดเป็นฟอร์มให้พิมพ์

อ่านอย่างเดียว — DD เป็นของ ATMS ค่าในนั้นคือค่าจริง ระบบนี้ไม่เขียนอะไรกลับ
v5 sync ทุก 4 ชม. ใบที่เพิ่งเปิดใน ATMS จึงยังไม่มี → "ไม่พบ" ไม่ใช่ error และไม่บล็อกใบ
"""
from __future__ import annotations

import logging
import os
from decimal import Decimal
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)

_TIMEOUT_MS = 3000
_MAX_TIME_MS = 2500
_MAX_LINES = 500
_CENT = Decimal("0.01")

PROJECTION = {
    "_id": 0, "DD": 1, "วันที่": 1, "คลังสินค้า": 1, "ซัพพลายเออร์": 1, "รหัสสินค้า": 1,
    "ชื่อสินค้า": 1, "กลุ่มสินค้า": 1, "รับ": 1, "ราคาทุน": 1, "ยอดเงิน": 1,
    "เลขรถ": 1, "ทะเบียน": 1, "พจส.": 1, "หมายเหตุ": 1, "หมายเหตุย่อย": 1, "PR": 1, "PO": 1,
}


@lru_cache(maxsize=1)
def _collection():
    from pymongo import MongoClient
    uri = os.getenv("MONGODB_URI")
    if not uri:
        raise RuntimeError("ไม่ได้ตั้งค่า MONGODB_URI")
    client = MongoClient(uri, serverSelectionTimeoutMS=_TIMEOUT_MS,
                         connectTimeoutMS=_TIMEOUT_MS, socketTimeoutMS=_TIMEOUT_MS)
    return client["atms"]["stockmovement_v5"]


def _money(value) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(_CENT)
    except Exception:
        return Decimal("0.00")


def fetch_dd(dd_no: str, atms_spec: Optional[dict] = None) -> dict:
    """คืนรูปทรงเดียวกันเสมอ ไม่ว่าจะเจอ ไม่เจอ หรือ Mongo ล่ม — หน้าจอจึงไม่ต้องเดา"""
    spec = atms_spec or {}
    empty = {"found": False, "dd_no": dd_no, "received_date": None, "warehouse": None,
             "supplier": None, "pr": None, "po": None, "lines": [], "total": Decimal("0.00"),
             "unit": spec.get("unit"), "vat": spec.get("vat"), "error": None}
    number = (dd_no or "").strip()
    if not number:
        return empty
    try:
        rows = list(_collection().find({"DD": number}, PROJECTION).limit(_MAX_LINES)
                    .max_time_ms(_MAX_TIME_MS))
    except Exception as exc:
        logger.warning("DD lookup failed for %s: %s", number, exc)
        return {**empty, "error": "ดึงข้อมูล DD จาก ATMS ไม่ได้ ลองใหม่อีกครั้ง"}
    if not rows:
        return empty

    lines, total = [], Decimal("0.00")
    for row in rows:
        amount = _money(row.get("ยอดเงิน"))
        total += amount
        lines.append({
            "sku": row.get("รหัสสินค้า"), "name": row.get("ชื่อสินค้า"),
            "product_group": row.get("กลุ่มสินค้า"), "qty": _money(row.get("รับ")),
            "unit": spec.get("unit"), "rate": _money(row.get("ราคาทุน")), "amount": amount,
            "truck_code": row.get("เลขรถ"), "truck_plate": row.get("ทะเบียน"),
            "driver": row.get("พจส."), "remark": row.get("หมายเหตุ"),
            "sub_remark": row.get("หมายเหตุย่อย"), "pr": row.get("PR"), "po": row.get("PO"),
        })
    first = rows[0]
    # PR/PO อยู่ระดับเอกสาร แต่ ATMS ลงไว้รายบรรทัด และบางบรรทัดเว้นว่าง — หยิบค่าแรกที่มี
    pick = lambda key: next((str(r.get(key)).strip() for r in rows if str(r.get(key) or "").strip()), None)
    return {
        "found": True, "dd_no": number, "received_date": first.get("วันที่"),
        "warehouse": first.get("คลังสินค้า"), "supplier": first.get("ซัพพลายเออร์"),
        "pr": pick("PR"), "po": pick("PO"),
        "lines": lines, "total": total.quantize(_CENT),
        "unit": spec.get("unit"), "vat": spec.get("vat"), "error": None,
    }


def compare_total(dd: dict, claim_amount) -> dict:
    """ยอดใน DD ต้องเท่ายอดใบเบิก — ไม่เท่าไม่บล็อก แต่ต้องเตือนให้คนเห็นส่วนต่าง"""
    if not dd.get("found"):
        return {"match": False, "difference": _money(claim_amount)}
    difference = (_money(claim_amount) - _money(dd.get("total"))).copy_abs()
    return {"match": difference == Decimal("0.00"), "difference": difference}
