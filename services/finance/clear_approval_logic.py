"""อนุมัติการเคลียร์เงิน — ผู้เบิกส่งเคลียร์แล้วต้องผ่านหัวหน้าก่อนถึงบัญชี
(ผู้ใช้สั่ง 19/10/2026: "ตอนเคลียร์ ก่อนถึงบัญชี ต้องผ่านการอนุมัติตามขั้น เหมือนตอนขอเบิก")

กติกาที่ตกลงกันไว้
- ชั้นอนุมัติตัดด้วย **ยอดที่ใช้จริง** (amount_actual) ไม่ใช่ยอดที่เบิกไป
- ขั้นตอนเหมือนตอนขอเบิกทุกอย่าง: ขั้น 1 หัวหน้าถัดไป · ขั้น 2 ผู้มีอำนาจตาม TOA
- บัญชีส่งกลับแก้ไข แล้วผู้เบิกแก้ส่งใหม่ → ขึ้นอนุมัติใหม่ **เฉพาะตอนยอดใหม่ข้ามชั้น**

ไม่มี DB ไม่มี FastAPI — รับ dict ล้วน เพื่อให้เทสต์ได้โดยไม่ต้องต่อฐาน
"""
from __future__ import annotations

from typing import Mapping, Optional

from services.finance import approval_logic as rules
from services.finance.advance_logic import AdvanceRuleError, InvalidTransition, NotAllowed

STEP_LEADER = rules.STEP_LEADER

NOT_OWNER = "เฉพาะผู้เบิกเงินเท่านั้นที่ส่งเคลียร์ได้"
NOT_ELIGIBLE = "คุณไม่มีอำนาจอนุมัติการเคลียร์ของคำขอนี้"
ALREADY_APPROVED = "คุณอนุมัติขั้นนี้ไปแล้ว"
NOT_PENDING = "รายการนี้ไม่ได้รออนุมัติการเคลียร์"
REJECT_REMARK_REQUIRED = "กรุณาระบุเหตุผลที่ไม่อนุมัติการเคลียร์"


def plan(tiers, people: Mapping[str, Mapping], mappings, requester_employee_id: str,
         amount_actual) -> dict:
    """สายอนุมัติของการเคลียร์ ตัดชั้นด้วยยอดที่ใช้จริง — คืนรูปเดียวกับตอนขอเบิก

    ใช้กติกาเดิม (ขั้น 2 เฉพาะตอน TOA เรียก) กฎ "ทุกใบต้องมีสองขั้น" ของ 19/10/2026
    ผู้ใช้สั่งไว้กับการ "ขอเบิก" เท่านั้น การเคลียร์ยังตัดชั้นตามยอดใช้จริงอย่างเดียว
    """
    return rules.evaluate(tiers, people, mappings, requester_employee_id, amount_actual,
                          force_second=False)


def clause_for(tiers, amount_actual) -> str:
    return rules.pick_tier(tiers, amount_actual)["clause"]


def needs_approval(plan_info: Mapping) -> bool:
    """ตอนนี้มีขั้น 1 เสมอ แต่เก็บไว้เป็นประตูเดียวเผื่อกติกาเปลี่ยน"""
    return bool(plan_info.get("steps"))


def needs_reapproval(approved_clause: Optional[str], new_clause: str) -> bool:
    """แก้แล้วส่งใหม่: ขึ้นอนุมัติใหม่เฉพาะตอนข้ามชั้น (ผู้ใช้เลือก 19/10/2026)"""
    if approved_clause is None:
        return True
    return approved_clause != new_clause


def step_spec(plan_info: Mapping, step: int) -> Mapping:
    for spec in plan_info.get("steps") or ():
        if spec["step"] == step:
            return spec
    raise InvalidTransition(NOT_PENDING)


def next_step(plan_info: Mapping, step: int) -> Optional[int]:
    """ขั้นถัดไป หรือ None ถ้าขั้นนี้คือขั้นสุดท้าย"""
    steps = [s["step"] for s in plan_info.get("steps") or ()]
    later = [s for s in steps if s > step]
    return min(later) if later else None


def check_submit(status, *, is_owner: bool) -> None:
    if not is_owner:
        raise NotAllowed(NOT_OWNER)


def can_approve(plan_info: Mapping, people: Mapping[str, Mapping], mappings,
                requester_employee_id: str, approver_employee_id: str, step: int,
                already: tuple = ()) -> bool:
    """`already` = users.id ของคนที่อนุมัติขั้นก่อนหน้าในรอบนี้ — ห้ามคนเดิมกดสองขั้น"""
    spec = step_spec(plan_info, step)
    requester = people.get(requester_employee_id) or {}
    if rules.superadmin_may_approve(approver_employee_id, requester_employee_id, requester):
        return True
    approver = people.get(approver_employee_id)
    if approver is None:
        return False
    return rules.can_approve(approver, requester, spec["required_level"],
                             mappings.get(approver_employee_id, ()), already)


def check_approve(status_pending: bool, eligible: bool) -> None:
    if not status_pending:
        raise InvalidTransition(NOT_PENDING)
    if not eligible:
        raise NotAllowed(NOT_ELIGIBLE)


def check_reject(status_pending: bool, eligible: bool, remark) -> str:
    check_approve(status_pending, eligible)
    text = (remark or "").strip()
    if not text:
        raise AdvanceRuleError(REJECT_REMARK_REQUIRED)
    return text
