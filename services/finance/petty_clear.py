"""หัวหน้าที่อนุมัติเคลียร์ของเงินสดย่อย — ตามสายงานของเจ้าของเงินสด (flow v2 29/10/2026)

ใช้ engine ของ Advance ทั้งชุด (approval_logic) โดยให้เจ้าของเงินสดเป็น "ผู้ขอ":
แผนกเดียวกันก่อน ไม่มีใครในแผนกค่อยขยายทั้งฝ่าย · ผู้ดูแลฝ่ายกับระดับ 9 กดได้ · เจ้าตัวกดเองไม่ได้
ฟังก์ชันในไฟล์นี้ไม่แตะฐานข้อมูล — people/mappings มาจาก approval_repo.load_context
"""
from __future__ import annotations

from typing import Iterable, Mapping, Optional

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


# ---- หัวหน้าอนุมัติใบก่อนโอน (flow v3 → v3.2 TOA 7/11/2026) ----
# ระดับที่ต้องอนุมัติเอาจากตาราง TOA เดียวกับ Advance (fin_approval_tiers ตามยอด) และต้องสูงกว่าผู้ขอเบิก
# ถ้า TOA ให้อำนาจเจ้าของเงินสดอนุมัติใบนี้ได้เอง → ไม่มีขั้นหัวหน้า ใบไปรอเจ้าของเงินสดทีเดียว
# (ผู้ใช้สั่ง 7/11/2026 "ถ้า TOA ถูก ไม่ต้องผ่านหัวหน้าอนุมัติ" — ยกเลิกกติกา "คนอนุมัติ ≠ คนโอน" ของ 30/10)
# ถ้ายอดเกินอำนาจเจ้าของเงินสด หรือเขาไม่ได้อยู่ในสายงานของผู้ขอเบิก → รอหัวหน้าระดับตาม TOA เหมือนเดิม

def toa_required_level(tiers: Iterable[Mapping], requester_level: Optional[int], amount) -> int:
    """ระดับที่ TOA ต้องการสำหรับยอดนี้ สูงกว่าผู้ขอเบิกอย่างน้อยหนึ่งระดับ (สูตร Advance)
    ไม่มีตาราง TOA (เครื่องทดสอบ/ยอดผิด) → ถอยไปสูตรเดิมของ flow v3"""
    from services.finance import petty_logic as L  # petty_logic ไม่ import ไฟล์นี้ แต่กันวนไว้ก่อน
    tiers = list(tiers or ())
    if not tiers:
        return L.head_required_level(requester_level)
    try:
        tier = rules.pick_tier(tiers, amount)
    except rules.AdvanceRuleError:
        return L.head_required_level(requester_level)
    return min(max(int(tier["min_level"]), (requester_level or 0) + 1), L.TOP_LEVEL)


def holder_covers(people: Mapping[str, Mapping], mappings: Mapping[str, Iterable[int]], tiers: Iterable[Mapping],
                  requester_id: Optional[str], holder_id: Optional[str], amount) -> bool:
    """TOA ให้เจ้าของเงินสดอนุมัติใบนี้เองได้ไหม (ระดับถึง + อยู่ในสายงานของผู้ขอเบิก) → ข้ามขั้นหัวหน้า
    เจ้าของเงินสดเปิดใบเอง = ไม่ครอบ (ห้ามอนุมัติใบตัวเอง) → ไปหาหัวหน้าของเขาตามปกติ"""
    requester = people.get(requester_id) if requester_id else None
    holder = people.get(holder_id) if holder_id else None
    if requester is None or holder is None:
        return False
    required = toa_required_level(tiers, requester.get("level"), amount)
    return rules.may_approve(list(people.values()), holder, requester, required, mappings)


def head_rule(people: Mapping[str, Mapping], mappings: Mapping[str, Iterable[int]], requester_id: str,
              holder_id: Optional[str], tiers: Iterable[Mapping] = (), amount=None) -> tuple[int, tuple]:
    """(ระดับที่ต้องอนุมัติขั้นหัวหน้า, users.id ที่ห้ามกด) — ไม่กันเจ้าของเงินสดแล้ว (TOA ตัดสินแทน)"""
    requester = people.get(requester_id) or {}
    return toa_required_level(tiers, requester.get("level"), amount), ()


def can_head(people: Mapping[str, Mapping], mappings: Mapping[str, Iterable[int]], requester_id: str,
             holder_id: Optional[str], approver_id: str, tiers: Iterable[Mapping] = (), amount=None) -> bool:
    requester = people.get(requester_id)
    approver = people.get(approver_id)
    if requester is None or approver is None:
        return False
    required, excluded = head_rule(people, mappings, requester_id, holder_id, tiers, amount)
    return rules.may_approve(list(people.values()), approver, requester, required, mappings, excluded)


def head_notify(people: Mapping[str, Mapping], mappings: Mapping[str, Iterable[int]], requester_id: str,
                holder_id: Optional[str], tiers: Iterable[Mapping] = (), amount=None) -> list:
    """คนที่ได้อีเมลขั้นหัวหน้า: ชั้นต่ำสุดที่กดได้ สาขาเดียวกับผู้ขอเบิกก่อน"""
    requester = people.get(requester_id)
    if requester is None:
        return []
    required, excluded = head_rule(people, mappings, requester_id, holder_id, tiers, amount)
    eligible = rules.eligible_approvers(list(people.values()), requester, required, mappings, excluded)
    return rules.notify_approvers(eligible, required, requester=requester)
