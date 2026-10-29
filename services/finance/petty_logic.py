"""กฎล้วนของเบิกเงินสดย่อย — ไม่มี DB ไม่มี FastAPI (เทสต์ได้โดยไม่ต้องต่อฐาน)

อนุมัติขั้นเดียวจบ ไม่ใช้ TOA/approval engine — spec §4.1
"""
from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Mapping, Optional

PENDING_APPROVAL = "PENDING_APPROVAL"
RETURNED = "RETURNED"
APPROVED = "APPROVED"
CLEAR_PENDING = "CLEAR_PENDING"
AWAITING_REVIEW = "AWAITING_REVIEW"
SENT_BACK = "SENT_BACK"
REVIEWED = "REVIEWED"
CLOSED = "CLOSED"
TRANSFERRED = "TRANSFERRED"  # flow เดิม (โอนแยกขั้น) — เลิกใช้ 29/10/2026 เก็บไว้ให้ป้ายของ log เก่าอ่านออก

def bkk_today() -> date:
    """วันนี้ตามเวลาไทย — ใช้ตัดสินว่าวันที่ในรายการเป็นอนาคตหรือไม่ (ไม่ใช้ UTC ที่ช้ากว่า 7 ชม.)"""
    return datetime.now(ZoneInfo("Asia/Bangkok")).date()


# flow v2 (ผู้ใช้สั่ง 29/10/2026): เจ้าของเงินสดอนุมัติพร้อมจ่าย → ส่งเคลียร์เป็นรอบ → หัวหน้าอนุมัติเคลียร์
# → บัญชีตรวจ (ผ่าน → ตั้งเบิก/ทำจ่ายทั้งรอบ → ปิด · ไม่ผ่าน → ตีกลับเจ้าหน้าที่ แก้แล้วส่งตรงกลับบัญชี)
STATUSES = (PENDING_APPROVAL, RETURNED, APPROVED, CLEAR_PENDING, AWAITING_REVIEW, SENT_BACK, REVIEWED, CLOSED)

STATUS_LABELS = {
    PENDING_APPROVAL: "รอเจ้าของเงินสดอนุมัติ",
    RETURNED: "ตีกลับผู้เบิก",
    APPROVED: "อนุมัติจ่ายแล้ว รอส่งเคลียร์",
    CLEAR_PENDING: "รอหัวหน้าอนุมัติเคลียร์",
    AWAITING_REVIEW: "รอบัญชีตรวจ",
    SENT_BACK: "บัญชีตีกลับ รอแก้ไข",
    REVIEWED: "บัญชีตรวจผ่าน รอตั้งเบิก",
    CLOSED: "ปิดแล้ว",
    TRANSFERRED: "โอนแล้ว (ขั้นตอนเดิม)",
}

ALLOWED = {
    PENDING_APPROVAL: (APPROVED, RETURNED),
    RETURNED: (PENDING_APPROVAL,),
    APPROVED: (CLEAR_PENDING,),
    CLEAR_PENDING: (AWAITING_REVIEW, APPROVED),  # หัวหน้าไม่อนุมัติ → กลับไปให้เจ้าของเงินสดจัดรอบใหม่
    AWAITING_REVIEW: (REVIEWED, SENT_BACK),
    SENT_BACK: (AWAITING_REVIEW,),
    REVIEWED: (CLOSED,),
    CLOSED: (),
}

# ---- รอบส่งเคลียร์ (fin_petty_batches) — ชุดใบของกองทุนเดียว พร้อมใบปะหน้า ----
BATCH_CLEAR_PENDING = "CLEAR_PENDING"
BATCH_CLEAR_REJECTED = "CLEAR_REJECTED"
BATCH_AWAITING_REVIEW = "AWAITING_REVIEW"
BATCH_REVIEWED = "REVIEWED"
BATCH_VOUCHERED = "VOUCHERED"
BATCH_CLOSED = "CLOSED"

BATCH_STATUSES = (BATCH_CLEAR_PENDING, BATCH_CLEAR_REJECTED, BATCH_AWAITING_REVIEW, BATCH_REVIEWED,
                  BATCH_VOUCHERED, BATCH_CLOSED)

BATCH_STATUS_LABELS = {
    BATCH_CLEAR_PENDING: "รอหัวหน้าอนุมัติเคลียร์",
    BATCH_CLEAR_REJECTED: "หัวหน้าไม่อนุมัติ",
    BATCH_AWAITING_REVIEW: "รอบัญชีตรวจ",
    BATCH_REVIEWED: "ตรวจผ่าน รอตั้งเบิก",
    BATCH_VOUCHERED: "ตั้งเบิกแล้ว รอทำจ่าย",
    BATCH_CLOSED: "จ่ายแล้ว ปิดรอบ",
}

BATCH_ALLOWED = {
    BATCH_CLEAR_PENDING: (BATCH_AWAITING_REVIEW, BATCH_CLEAR_REJECTED),
    BATCH_CLEAR_REJECTED: (),
    BATCH_AWAITING_REVIEW: (BATCH_REVIEWED,),
    BATCH_REVIEWED: (BATCH_VOUCHERED,),
    BATCH_VOUCHERED: (BATCH_CLOSED,),
    BATCH_CLOSED: (),
}

# เลขเอกสารของรอบที่เช็กซ้ำได้ (เตือนอย่างเดียว เหมือน Advance)
ROUND_DOC_NO_FIELDS = ("voucher_no", "pay_doc_no")

# แก้เลขเอกสารที่พิมพ์ผิดได้หลังตั้งเบิก (แบบปุ่มแก้ไขเลขเอกสารของ Advance) · เลขที่ทำจ่ายแก้ได้เมื่อจ่ายแล้ว
ROUND_DOC_LABELS = {"voucher_no": "เลขที่ตั้งเบิก", "voucher_date": "วันที่ตั้งเบิก", "pay_doc_no": "เลขที่ทำจ่าย"}
EDITABLE_DOC_FIELDS = {
    BATCH_VOUCHERED: ("voucher_no", "voucher_date"),
    BATCH_CLOSED: ("voucher_no", "voucher_date", "pay_doc_no"),
}


def doc_number_changes(status: str, current: Mapping, wanted: Mapping) -> dict:
    """{ช่อง: (ค่าเดิม, ค่าใหม่)} เฉพาะช่องที่เปลี่ยนจริง · ช่องว่างที่ส่งมาซ้ำค่าเดิมไม่นับ (หน้าเว็บส่งทุกช่องได้)"""
    allowed = EDITABLE_DOC_FIELDS.get(status)
    if allowed is None:
        label = BATCH_STATUS_LABELS.get(status, status)
        raise PettyRuleError(f"แก้เลขเอกสารได้หลังตั้งเบิกแล้วเท่านั้น รอบนี้สถานะ {label}", 409)
    diffs = {k: (current.get(k), v) for k, v in wanted.items() if current.get(k) != v}
    if any(k not in allowed for k in diffs):
        raise PettyRuleError("รอบนี้ยังไม่ได้จ่ายเงิน ยังไม่มีเลขที่ทำจ่ายให้แก้", 409)
    return diffs


def _doc_text(value) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return str(value)


def doc_changes_text(diffs: Mapping) -> str:
    """"เลขที่ตั้งเบิก: A → B · วันที่ตั้งเบิก: 29/10/2026 → 30/10/2026" สำหรับประวัติรอบ"""
    order = [k for k in ROUND_DOC_LABELS if k in diffs]
    return " · ".join(f"{ROUND_DOC_LABELS[k]}: {_doc_text(diffs[k][0])} → {_doc_text(diffs[k][1])}"
                      for k in order)

MANAGER_LEVEL = 5
TOP_LEVEL = 9


def clear_required_level(holder_level: Optional[int]) -> int:
    """หัวหน้าที่อนุมัติเคลียร์ = ผู้จัดการตามสายงาน: อย่างน้อยระดับผู้จัดการ และต้องสูงกว่าเจ้าของเงินสด"""
    return min(max(MANAGER_LEVEL, (holder_level or 0) + 1), TOP_LEVEL)


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


def assert_batch_transition(current: str, target: str) -> None:
    """กติกาเดียวกับใบ แต่สำหรับรอบ · ไม่ได้ก็ 409 พร้อมสถานะรอบเป็นคำไทย"""
    if current not in BATCH_ALLOWED:
        raise PettyRuleError(f"สถานะรอบไม่ถูกต้อง ({current})", 409)
    if target not in BATCH_ALLOWED[current]:
        label = BATCH_STATUS_LABELS.get(current, current)
        raise PettyRuleError(f"ทำรายการนี้ไม่ได้ รอบนี้สถานะ {label} อยู่แล้ว", 409)
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


# 01 สกท = BK ตามคลังกรุงเทพใน ATMS (BKDD…) · 04 ศรย / 05 สขก ยังไม่ยืนยัน
BRANCH_LETTERS = {"01": "BK", "02": "SB", "03": "LB"}


def branch_letters(branch: str) -> str:
    """ตัวอักษรสาขาแบบที่ ATMS ใช้ (BKDD / SBDD / LBDD) · สาขาที่ยังไม่ยืนยัน ใช้เลขเดิมไปก่อน"""
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
