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
