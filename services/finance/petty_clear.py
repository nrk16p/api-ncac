"""หัวหน้าที่อนุมัติเคลียร์ของเงินสดย่อย — ตามสายงานของเจ้าของเงินสด (flow v2 29/10/2026)

ใช้ engine ของ Advance ทั้งชุด (approval_logic) โดยให้เจ้าของเงินสดเป็น "ผู้ขอ":
แผนกเดียวกันก่อน ไม่มีใครในแผนกค่อยขยายทั้งฝ่าย · ผู้ดูแลฝ่ายกับระดับ 9 กดได้ · เจ้าตัวกดเองไม่ได้
ฟังก์ชันในไฟล์นี้ไม่แตะฐานข้อมูล — people/mappings มาจาก approval_repo.load_context
"""
from __future__ import annotations

from typing import Iterable, Mapping

from services.finance import approval_logic as rules


def can_clear(people: Mapping[str, Mapping], mappings: Mapping[str, Iterable[int]], holder_id: str,
              required: int, approver_id: str) -> bool:
    holder = people.get(holder_id)
    approver = people.get(approver_id)
    if holder is None or approver is None:
        return False
    return rules.may_approve(list(people.values()), approver, holder, required, mappings)


def clear_notify(people: Mapping[str, Mapping], mappings: Mapping[str, Iterable[int]], holder_id: str,
                 required: int) -> list:
    """คนที่ควรได้อีเมล: ชั้นต่ำสุดที่กดได้ สาขาเดียวกับเจ้าของเงินสดก่อน (กติกาเดียวกับ Advance)"""
    holder = people.get(holder_id)
    if holder is None:
        return []
    eligible = rules.eligible_approvers(list(people.values()), holder, required, mappings)
    return rules.notify_approvers(eligible, required, requester=holder)


# ---- หัวหน้าอนุมัติใบก่อนโอน (flow v3) ----
# คนอนุมัติกับคนโอนต้องเป็นคนละคน (ผู้ใช้สั่ง 30/10/2026): เจ้าของเงินสดไม่เคยเป็นหัวหน้าของใบในวงเงินตัวเอง
# ถ้าเขาเป็นหัวหน้าของผู้ขอเบิก ข้ามไปอีกระดับ (สูงกว่าเจ้าของเงินสด) — ยานยนต์: ผู้จัดการฝ่ายอนุมัติ เจ้าของเงินสดจ่าย

def head_rule(people: Mapping[str, Mapping], mappings: Mapping[str, Iterable[int]], requester_id: str,
              holder_id: str) -> tuple[int, tuple]:
    """(ระดับที่ต้องอนุมัติ, users.id ที่ห้ามกด) ของขั้นหัวหน้า"""
    from services.finance import petty_logic as L  # petty_logic ไม่ import ไฟล์นี้ แต่กันวนไว้ก่อน
    requester = people.get(requester_id) or {}
    holder = people.get(holder_id)
    required = L.head_required_level(requester.get("level"))
    if holder is None:
        return required, ()
    excluded = (holder["id"],) if holder.get("id") is not None else ()
    if requester and rules.may_approve(list(people.values()), holder, requester, required, mappings):
        required = min(max(required, (holder.get("level") or 0) + 1), L.TOP_LEVEL)
    return required, excluded


def can_head(people: Mapping[str, Mapping], mappings: Mapping[str, Iterable[int]], requester_id: str,
             holder_id: str, approver_id: str) -> bool:
    requester = people.get(requester_id)
    approver = people.get(approver_id)
    if requester is None or approver is None or str(approver_id) == str(holder_id):
        return False
    required, excluded = head_rule(people, mappings, requester_id, holder_id)
    return rules.may_approve(list(people.values()), approver, requester, required, mappings, excluded)


def head_notify(people: Mapping[str, Mapping], mappings: Mapping[str, Iterable[int]], requester_id: str,
                holder_id: str) -> list:
    """คนที่ได้อีเมลขั้นหัวหน้า: ชั้นต่ำสุดที่กดได้ สาขาเดียวกับผู้ขอเบิกก่อน (ไม่มีเจ้าของเงินสด)"""
    requester = people.get(requester_id)
    if requester is None:
        return []
    required, excluded = head_rule(people, mappings, requester_id, holder_id)
    eligible = rules.eligible_approvers(list(people.values()), requester, required, mappings, excluded)
    return rules.notify_approvers(eligible, required, requester=requester)
