"""Pure rules: ADV approval by amount (menait-service spec 2026-09-29-finance-advance-v2-design.md §3).

No DB, no FastAPI. Inputs are plain dicts so everything is unit-testable.
Person: {"employee_id", "id", "level", "department_id", "active"}.
Tier:   {"clause", "amount_max" (Decimal | None = no cap), "min_level", "approver_label", "sort_order"}.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping, Optional

from services.finance.advance_logic import AdvanceRuleError

ORG_WIDE_LEVEL = 9
TAB_MINE = "mine"
TAB_DELEGABLE = "delegable"

MSG_NO_TIERS = "ยังไม่ได้ตั้งค่าวงเงินอนุมัติ"
MSG_BAD_AMOUNT = "จำนวนเงินต้องมากกว่า 0"
MSG_NO_LEVEL = "ไม่พบระดับตำแหน่งของผู้ขอ"
MSG_NO_APPROVER = "ไม่พบผู้อนุมัติที่มีสิทธิ์สำหรับวงเงินนี้"


def to_amount(value) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise AdvanceRuleError(MSG_BAD_AMOUNT)
    if value is None or not amount.is_finite() or amount <= 0:
        raise AdvanceRuleError(MSG_BAD_AMOUNT)
    return amount


def pick_tier(tiers: Iterable[Mapping], amount) -> Mapping:
    value = to_amount(amount)
    ordered = sorted(tiers, key=lambda t: t["sort_order"])
    for tier in ordered:
        cap = tier.get("amount_max")
        if cap is None or value <= Decimal(str(cap)):
            return tier
    raise AdvanceRuleError(MSG_NO_TIERS)


def required_level(tier_min_level: int, requester_level: Optional[int]) -> int:
    if requester_level is None:
        raise AdvanceRuleError(MSG_NO_LEVEL)
    return max(tier_min_level, min(requester_level + 1, ORG_WIDE_LEVEL))


def can_approve(approver: Mapping, requester: Mapping, required: int, mapped_departments: Iterable[int]) -> bool:
    if not approver.get("active"):
        return False
    level = approver.get("level")
    if level is None or level < required:
        return False
    if approver.get("employee_id") == requester.get("employee_id"):
        return False
    if level >= ORG_WIDE_LEVEL:
        return True
    dept = requester.get("department_id")
    if dept is None:
        return False
    return approver.get("department_id") == dept or dept in set(mapped_departments)


def direct_level(people: Iterable[Mapping], requester: Mapping, required: int,
                 mappings: Mapping[str, Iterable[int]]) -> Optional[int]:
    levels = [p["level"] for p in people
              if can_approve(p, requester, required, mappings.get(p["employee_id"], ()))]
    return min(levels) if levels else None


def approval_tab(approver_level: int, direct: Optional[int]) -> str:
    return TAB_MINE if direct is not None and approver_level <= direct else TAB_DELEGABLE


def evaluate(tiers, people: Mapping[str, Mapping], mappings, requester_employee_id: str, amount) -> dict:
    tier = pick_tier(tiers, amount)
    requester = people.get(requester_employee_id)
    required = required_level(tier["min_level"], requester.get("level") if requester else None)
    direct = direct_level(people.values(), requester, required, mappings)
    if direct is None:
        raise AdvanceRuleError(MSG_NO_APPROVER)
    return {"clause": tier["clause"], "approver_label": tier["approver_label"], "min_level": tier["min_level"],
            "required_level": required, "direct_level": direct}
