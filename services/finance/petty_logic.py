"""กฎล้วนของเบิกเงินสดย่อย — ไม่มี DB ไม่มี FastAPI (เทสต์ได้โดยไม่ต้องต่อฐาน)

อนุมัติขั้นเดียวจบ ไม่ใช้ TOA/approval engine — spec §4.1
"""
from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Mapping

PENDING_APPROVAL = "PENDING_APPROVAL"
RETURNED = "RETURNED"
APPROVED = "APPROVED"
TRANSFERRED = "TRANSFERRED"
AWAITING_REVIEW = "AWAITING_REVIEW"
SENT_BACK = "SENT_BACK"
CLOSED = "CLOSED"

STATUSES = (PENDING_APPROVAL, RETURNED, APPROVED, TRANSFERRED, AWAITING_REVIEW, SENT_BACK, CLOSED)

STATUS_LABELS = {
    PENDING_APPROVAL: "รออนุมัติ",
    RETURNED: "ตีกลับผู้เบิก",
    APPROVED: "อนุมัติแล้ว รอโอน",
    TRANSFERRED: "โอนแล้ว รอเคลียร์เอกสาร",
    AWAITING_REVIEW: "รอบัญชีตรวจ",
    SENT_BACK: "ส่งกลับแก้ไข",
    CLOSED: "คืนยอดแล้ว",
}

ALLOWED = {
    PENDING_APPROVAL: (APPROVED, RETURNED),
    RETURNED: (PENDING_APPROVAL,),
    APPROVED: (TRANSFERRED,),
    TRANSFERRED: (AWAITING_REVIEW,),
    AWAITING_REVIEW: (CLOSED, SENT_BACK),
    SENT_BACK: (AWAITING_REVIEW,),
    CLOSED: (),
}


class PettyRuleError(Exception):
    def __init__(self, message: str, http_status: int = 422):
        super().__init__(message)
        self.http_status = http_status


def assert_transition(current: str, target: str) -> None:
    """ปล่อยผ่านเมื่อเปลี่ยนสถานะได้ · ไม่ได้ก็ 409 พร้อมบอกสถานะปัจจุบันเป็นคำไทย"""
    if current not in ALLOWED:
        raise PettyRuleError(f"สถานะปัจจุบันไม่ถูกต้อง ({current})", 409)
    if target not in ALLOWED[current]:
        label = STATUS_LABELS.get(current, current)
        raise PettyRuleError(f"ทำรายการนี้ไม่ได้ ใบนี้สถานะ {label} อยู่แล้ว", 409)
    return None


_PLATE_STRIP = re.compile(r"[^0-9A-Za-z฀-๿]")


def normalize_plate(raw: str | None) -> str:
    """ทะเบียน/รหัสรถให้เทียบกันได้: ตัดช่องว่าง ขีด จุด แล้วทำตัวอังกฤษเป็นพิมพ์ใหญ่

    มีไว้เพราะด่านซ้ำเข้มใช้ทะเบียนเป็นคีย์ ถ้าไม่ล้างก่อน '71 4285' กับ '71-4285' จะกลายเป็นสองคัน
    """
    if not raw:
        return ""
    return _PLATE_STRIP.sub("", str(raw)).upper()


normalize_code = normalize_plate  # รหัสรถภายในใช้กฎเดียวกัน (F014 / f014 / F-014)

_CENT = Decimal("0.01")


def line_amount(qty, rate) -> Decimal:
    return (Decimal(str(qty)) * Decimal(str(rate))).quantize(_CENT, rounding=ROUND_HALF_UP)


def claim_total(items: Iterable[Mapping]) -> Decimal:
    """ยอดรวมของใบ = ผลรวมของยอดรายบรรทัด *ที่ปัดแล้ว* เพื่อให้ตรงกับที่พิมพ์ออกกระดาษ"""
    total = sum((line_amount(i.get("qty", 1), i["rate"]) for i in items), Decimal("0"))
    return total.quantize(_CENT)


def period_of(items: Iterable[Mapping]) -> tuple[date | None, date | None]:
    dates = [i["event_date"] for i in items if i.get("event_date")]
    return (min(dates), max(dates)) if dates else (None, None)


def petty_form_code(branch: str, division: str) -> str:
    return f"PC-{branch}-{division}"


BRANCH_LETTERS = {"02": "SB", "03": "LB"}


def branch_letters(branch: str) -> str:
    """ตัวอักษรสาขาแบบที่ ATMS ใช้ (LBDD / SBDD) · สาขาที่ยังไม่ยืนยัน ใช้เลขเดิมไปก่อน"""
    code = (branch or "").strip()
    return BRANCH_LETTERS.get(code, code)


def format_form_id(branch: str, division: str, yymm: int, number: int) -> str:
    """เลขที่ใบ: สาขาเป็นตัวอักษรแบบ ATMS (LB/SB) ตามที่ผู้ใช้สั่ง 2026-10-13 — ไม่มี Project Code แยกอีก"""
    return f"{branch_letters(branch)}-{division}-{yymm:04d}-{number:03d}"


def batch_no(branch: str, division: str, yymm: int, number: int) -> str:
    return f"{branch_letters(branch)}-{division}-{yymm:04d}-B{number}"


def hard_dup_key(item: Mapping) -> tuple[str, str, str] | None:
    """คีย์ด่านซ้ำเข้ม: เลขที่บัตรชั่ง + เบอร์รถ + ทะเบียนรถ · ไม่มีเลขบัตร = ไม่มีคีย์ (ใช้ด่านอ่อนแทน)"""
    ref = (item.get("ref_no") or "").strip()
    if not ref:
        return None
    return (ref, normalize_code(item.get("truck_code")), normalize_plate(item.get("truck_plate")))
