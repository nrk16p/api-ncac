"""อนุมัติการเคลียร์เงิน — ผู้เบิกส่งเคลียร์แล้วต้องผ่านหัวหน้าก่อนถึงบัญชี
(ผู้ใช้สั่ง 19/10/2026: "ตอนเคลียร์ ก่อนถึงบัญชี ต้องผ่านการอนุมัติตามขั้น เหมือนตอนขอเบิก")

กติกาที่ตกลงกันไว้
- ขั้นอนุมัติ **เหมือนตอนขอเบิกเสมอ** คิดจากยอดในใบขอ ไม่ว่าใช้จริงเท่าไหร่ ใช้ 0 ก็ต้องผ่าน
  (ผู้ใช้สั่ง 26/10/2026 · เดิม 19/10 ตัดชั้นด้วยยอดที่ใช้จริง) — route เป็นคนส่งยอดในใบขอเข้ามา
- ขั้นตอนเหมือนตอนขอเบิกทุกอย่าง: ไล่ขั้นตาม TOA และขั้นพิเศษแผนกบัญชี
- บัญชีส่งกลับแก้ไข แล้วผู้เบิกแก้ส่งใหม่ → ขึ้นอนุมัติใหม่ **เฉพาะตอนยังไม่เคยผ่าน หรือชั้นเปลี่ยน**

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


def plan(tiers, people: Mapping[str, Mapping], mappings, requester_employee_id: str, amount) -> dict:
    """สายอนุมัติของการเคลียร์ — amount คือยอดในใบขอ จึงได้ขั้นเดียวกับตอนขอเบิกทุกอย่าง"""
    return rules.evaluate(tiers, people, mappings, requester_employee_id, amount)


def clause_for(tiers, amount) -> str:
    return rules.pick_tier(tiers, amount)["clause"]


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
    return rules.may_approve(people.values(), approver, requester, spec["required_level"], mappings, already,
                             spec.get("approver"), spec.get("stand_in_level"))


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
