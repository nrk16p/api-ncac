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
PERM_RECEIVE = "receive"                 # รับเงินคืน/ปิดรายการ (สายการเงินรับ)

# ผู้ดูแลระบบสูงสุดของงานเบิก Advance: ทุกปุ่ม รวมอนุมัติทุกขั้น (ผู้ใช้สั่ง 20/10/2026)
SUPERADMIN_EMPLOYEE_IDS = frozenset({"670108"})  # ณรงค์กรณ์ อัจฉริยญาติ (narongkorn.a)


def is_superadmin(employee_id) -> bool:
    return employee_id is not None and str(employee_id).strip() in SUPERADMIN_EMPLOYEE_IDS


ALL_PERMISSIONS = (
    PERM_VOUCHER, PERM_PAY, PERM_RETURN, PERM_CLEARING_REVIEW,
    PERM_CLOSE, PERM_PAYEE_ADMIN, PERM_OVERDUE_REMIND, PERM_RECEIVE,
)

PERMISSION_LABELS = {
    PERM_VOUCHER: "ตั้งเบิก",
    PERM_PAY: "จ่ายเงิน",
    PERM_RETURN: "ตีกลับให้ผู้เบิกแก้ไข",
    PERM_CLEARING_REVIEW: "ตรวจเคลียร์และส่งกลับแก้ไข",
    PERM_CLOSE: "ปิดรายการ",
    PERM_PAYEE_ADMIN: "จัดการบัญชีรับเงิน",
    PERM_OVERDUE_REMIND: "ส่งแจ้งเตือนเกินกำหนด",
    PERM_RECEIVE: "รับเงินคืน",
}

FINANCE_DEPARTMENT_ID = 4      # แผนกการเงิน
FINANCE_RECEIVE_DEPARTMENT_ID = 36  # แผนกการเงินรับ (master แยกแผนก 17/11/2026)
FINANCE_PAY_DEPARTMENT_ID = 37      # แผนกการเงินจ่าย
FINANCE_DEPARTMENT_IDS = frozenset({FINANCE_DEPARTMENT_ID, FINANCE_RECEIVE_DEPARTMENT_ID, FINANCE_PAY_DEPARTMENT_ID})
ACCOUNTING_DEPARTMENT_ID = 6   # แผนกบัญชี
DEPARTMENT_LABELS = {FINANCE_DEPARTMENT_ID: "แผนกการเงิน", ACCOUNTING_DEPARTMENT_ID: "แผนกบัญชี"}

PERMISSION_DEPARTMENT = {perm: ACCOUNTING_DEPARTMENT_ID for perm in ALL_PERMISSIONS}
PERMISSION_DEPARTMENT[PERM_PAY] = FINANCE_DEPARTMENT_ID
PERMISSION_DEPARTMENT[PERM_PAYEE_ADMIN] = FINANCE_DEPARTMENT_ID  # ผู้ใช้สั่ง 21/10/2026 บัญชีรับเงินเป็นงานการเงิน
PERMISSION_DEPARTMENT[PERM_RECEIVE] = FINANCE_DEPARTMENT_ID

# ---- การเงิน 2 สาย (ผู้ใช้สั่ง 7/11/2026 "เพิ่มอำนาจอนุมัติเป็น 2 สาย แยก การเงินรับ กับ การเงินจ่าย" ตั้งจาก /master) ----
# สายอ่านจากชื่อตำแหน่งใน master: มีคำว่า Payment/จ่าย = สายจ่าย · Receivable/รับ = สายรับ · ไม่มีคำบอก (Finance Manager) = ทั้งสองสาย
# สายจ่าย: จ่ายเงิน Advance · ทำจ่ายรอบเงินสดย่อย · จัดการบัญชีรับเงิน · สายรับ: รับเงินคืน + ปิดรายการ Advance (บัญชียังปิดได้เหมือนเดิม)
LINE_PAY = "payment"
LINE_RECEIVE = "receivable"
LINE_LABELS = {LINE_PAY: "การเงินจ่าย", LINE_RECEIVE: "การเงินรับ"}
PERMISSION_LINE = {PERM_PAY: LINE_PAY, PERM_PAYEE_ADMIN: LINE_PAY, PERM_RECEIVE: LINE_RECEIVE, PERM_CLOSE: LINE_RECEIVE}

_PAY_WORDS = ("payment", "payable", "จ่าย")
_RECEIVE_WORDS = ("receivable", "receiving", "รับ")


# แผนกบอกสายก่อน (ผู้ใช้สั่ง 17/11/2026): 37 การเงินจ่าย = สายจ่าย · 36 การเงินรับ = สายรับ · แผนก 4 ดูชื่อตำแหน่ง
DEPARTMENT_LINE = {FINANCE_PAY_DEPARTMENT_ID: "payment", FINANCE_RECEIVE_DEPARTMENT_ID: "receivable"}


def is_finance_department(department_id) -> bool:
    return _dept(department_id) in FINANCE_DEPARTMENT_IDS


def finance_line(position_name, department_id=None) -> str | None:
    """สายการเงิน: แผนก 36/37 บอกสายเลย · ไม่งั้นอ่านจากชื่อตำแหน่ง (ไทย/อังกฤษ) · None = ไม่ระบุ = ทำได้ทั้งสองสาย"""
    by_dept = DEPARTMENT_LINE.get(_dept(department_id))
    if by_dept is not None:
        return by_dept
    text = str(position_name or "").lower()
    pay = any(w in text for w in _PAY_WORDS)
    receive = any(w in text for w in _RECEIVE_WORDS)
    if pay and not receive:
        return LINE_PAY
    if receive and not pay:
        return LINE_RECEIVE
    return None


def line_allows(permission: str, position_name, department_id=None) -> bool:
    need = PERMISSION_LINE.get(permission)
    if need is None:
        return True
    line = finance_line(position_name, department_id)
    return line is None or line == need


DENIED = "คุณไม่มีสิทธิ์{label} ปุ่มนี้สำหรับ{dept}"


def denied_message(permission: str) -> str:
    dept = DEPARTMENT_LABELS[PERMISSION_DEPARTMENT.get(permission, ACCOUNTING_DEPARTMENT_ID)]
    line = PERMISSION_LINE.get(permission)
    if line is not None and PERMISSION_DEPARTMENT.get(permission) == FINANCE_DEPARTMENT_ID:
        dept = f"{dept} (สาย{LINE_LABELS[line]})"
    elif permission == PERM_CLOSE:
        dept = f"{dept} หรือการเงินสายรับ"
    return DENIED.format(label=PERMISSION_LABELS.get(permission, permission), dept=dept)


def _dept(department_id):
    try:
        return int(department_id)
    except (TypeError, ValueError):
        return None


def allowed(permission: str, employee_id, department_id, position_name=None) -> bool:
    """จ่ายเงิน + บัญชีรับเงิน = การเงินสายจ่าย · รับเงินคืน = การเงินสายรับ · ปิดรายการ = บัญชี หรือการเงินสายรับ
    ปุ่มอื่น = แผนกบัญชี · superadmin ได้ทุกปุ่ม · position_name ไม่ส่งมา = ไม่แยกสาย (เหมือนก่อน 7/11/2026)"""
    if is_superadmin(employee_id):
        return True
    if permission not in PERMISSION_DEPARTMENT:
        return False
    dept = _dept(department_id)
    finance = dept in FINANCE_DEPARTMENT_IDS
    if permission == PERM_CLOSE and finance:
        return line_allows(PERM_RECEIVE, position_name, dept)
    need_dept = PERMISSION_DEPARTMENT[permission]
    if not (dept == need_dept or (need_dept == FINANCE_DEPARTMENT_ID and finance)):
        return False
    # แยกสายเฉพาะคนแผนกการเงิน — บัญชีไม่สนคำว่า Payment/Receivable ในชื่อตำแหน่ง
    return not finance or line_allows(permission, position_name, dept)


def permissions_for(employee_id, department_id, position_name=None) -> list:
    """สิทธิ์ทั้งหมดที่คนนี้มี — FE ใช้ซ่อนปุ่มที่กดไม่ได้"""
    return [p for p in ALL_PERMISSIONS if allowed(p, employee_id, department_id, position_name)]


def position_text(user) -> str:
    """ชื่อตำแหน่งไทย+อังกฤษของ users.position (ORM) รวมเป็นข้อความเดียวให้ finance_line อ่าน"""
    pos = getattr(user, "position", None)
    if pos is None:
        return ""
    return f"{getattr(pos, 'position_name_th', '') or ''} {getattr(pos, 'position_name_en', '') or ''}"
