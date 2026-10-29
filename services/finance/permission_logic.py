"""สิทธิ์กดปุ่มของงานเบิกเงิน Advance ตามแผนก (ผู้ใช้สั่ง 21/10/2026 "ตั้งง่าย ๆ ตาม department")

บันทึกจ่ายเงินกับจัดการบัญชีรับเงินเป็นของแผนกการเงิน (4) ปุ่มอื่นทั้งหมดเป็นของแผนกบัญชี (6) · superadmin กดได้ทุกปุ่ม
เลิกใช้ตาราง fin_permissions รายคนแล้ว (ตารางยังอยู่ แต่ระบบไม่อ่าน)
ไม่มี DB ไม่มี FastAPI — เทสต์ได้โดยไม่ต้องต่อฐาน
"""
from __future__ import annotations


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

FINANCE_DEPARTMENT_ID = 4      # แผนกการเงิน
ACCOUNTING_DEPARTMENT_ID = 6   # แผนกบัญชี
DEPARTMENT_LABELS = {FINANCE_DEPARTMENT_ID: "แผนกการเงิน", ACCOUNTING_DEPARTMENT_ID: "แผนกบัญชี"}

PERMISSION_DEPARTMENT = {perm: ACCOUNTING_DEPARTMENT_ID for perm in ALL_PERMISSIONS}
PERMISSION_DEPARTMENT[PERM_PAY] = FINANCE_DEPARTMENT_ID
PERMISSION_DEPARTMENT[PERM_PAYEE_ADMIN] = FINANCE_DEPARTMENT_ID  # ผู้ใช้สั่ง 21/10/2026 บัญชีรับเงินเป็นงานการเงิน

DENIED = "คุณไม่มีสิทธิ์{label} ปุ่มนี้สำหรับ{dept}"


def denied_message(permission: str) -> str:
    return DENIED.format(label=PERMISSION_LABELS.get(permission, permission),
                         dept=DEPARTMENT_LABELS[PERMISSION_DEPARTMENT.get(permission, ACCOUNTING_DEPARTMENT_ID)])


def _dept(department_id):
    try:
        return int(department_id)
    except (TypeError, ValueError):
        return None


def allowed(permission: str, employee_id, department_id) -> bool:
    """จ่ายเงิน + บัญชีรับเงิน = แผนกการเงิน · ปุ่มอื่น = แผนกบัญชี · superadmin ได้ทุกปุ่ม"""
    if is_superadmin(employee_id):
        return True
    if permission not in PERMISSION_DEPARTMENT:
        return False
    return _dept(department_id) == PERMISSION_DEPARTMENT[permission]


def permissions_for(employee_id, department_id) -> list:
    """สิทธิ์ทั้งหมดที่คนนี้มี — FE ใช้ซ่อนปุ่มที่กดไม่ได้"""
    return [p for p in ALL_PERMISSIONS if allowed(p, employee_id, department_id)]
