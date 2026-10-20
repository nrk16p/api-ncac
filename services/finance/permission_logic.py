"""สิทธิ์ตามหน้าที่ของงานเบิกเงิน Advance (ผู้ใช้ตัดสิน 2026-10-19).

เดิมทุกคนในแผนกการเงิน/บัญชีกดได้ทุกปุ่ม ตอนนี้แยกเป็นสิทธิ์รายหน้าที่
ไม่มี DB ไม่มี FastAPI — รับ dict ล้วน ๆ เพื่อให้เทสต์ได้โดยไม่ต้องต่อฐาน
"""
from __future__ import annotations

from typing import Iterable, Mapping

PERM_VOUCHER = "voucher"                 # ตั้งเบิก
PERM_PAY = "pay"                         # จ่ายเงิน
PERM_RETURN = "return"                   # ตีกลับให้ผู้เบิกแก้ไข
PERM_CLEARING_REVIEW = "clearing_review" # ตรวจเคลียร์ · ส่งกลับแก้ไข
PERM_CLOSE = "close"                     # ปิดรายการ (รับเงินคืนครบ)
PERM_PAYEE_ADMIN = "payee_admin"         # จัดการบัญชีรับเงิน (Master + อนุมัติคำขอ)
PERM_OVERDUE_REMIND = "overdue_remind"   # ส่งแจ้งเตือนเกินกำหนด

# ผู้ดูแลระบบสูงสุดของงานเบิก Advance: ทุกปุ่ม รวมอนุมัติทุกขั้น (ผู้ใช้สั่ง 20/10/2026)
SUPERADMIN_EMPLOYEE_IDS = frozenset({"670108"})  # ณรงค์กรณ์ อัจฉริยญาติ (narongkorn.a)


def is_superadmin(employee_id) -> bool:
    return employee_id is not None and str(employee_id).strip() in SUPERADMIN_EMPLOYEE_IDS


ALL_PERMISSIONS = (
    PERM_VOUCHER, PERM_PAY, PERM_RETURN, PERM_CLEARING_REVIEW,
    PERM_CLOSE, PERM_PAYEE_ADMIN, PERM_OVERDUE_REMIND,
)

PERMISSION_LABELS = {
    PERM_VOUCHER: "ตั้งเบิก",
    PERM_PAY: "จ่ายเงิน",
    PERM_RETURN: "ตีกลับให้ผู้เบิกแก้ไข",
    PERM_CLEARING_REVIEW: "ตรวจเคลียร์และส่งกลับแก้ไข",
    PERM_CLOSE: "ปิดรายการ",
    PERM_PAYEE_ADMIN: "จัดการบัญชีรับเงิน",
    PERM_OVERDUE_REMIND: "ส่งแจ้งเตือนเกินกำหนด",
}

DENIED = "คุณไม่มีสิทธิ์{label} กรุณาติดต่อผู้ดูแลระบบ"


def denied_message(permission: str) -> str:
    return DENIED.format(label=PERMISSION_LABELS.get(permission, permission))


def is_configured(permission: str, grants: Mapping[str, Iterable[str]]) -> bool:
    """ยังไม่มีใครถูกกำหนดสิทธิ์นี้ = ยังไม่ได้ตั้งค่า (ใช้กติกาเดิมไปก่อน)."""
    return bool(set(grants.get(permission) or ()))


def allowed(permission: str, employee_id: str, grants: Mapping[str, Iterable[str]],
            is_finance: bool) -> bool:
    """ถ้าสิทธิ์นี้ยังไม่ได้ตั้งค่า ใช้กติกาเดิม (ใครอยู่การเงิน/บัญชีก็ทำได้)
    เพื่อไม่ให้ระบบล็อกตัวเองตอน deploy ก่อนรัน SQL · superadmin ได้ทุกสิทธิ์เสมอ"""
    if is_superadmin(employee_id):
        return True
    if not is_configured(permission, grants):
        return bool(is_finance)
    return employee_id is not None and employee_id in set(grants.get(permission) or ())


def permissions_for(employee_id: str, grants: Mapping[str, Iterable[str]],
                    is_finance: bool) -> list:
    """สิทธิ์ทั้งหมดที่คนนี้มี — FE ใช้ซ่อนปุ่มที่กดไม่ได้"""
    return [p for p in ALL_PERMISSIONS if allowed(p, employee_id, grants, is_finance)]
