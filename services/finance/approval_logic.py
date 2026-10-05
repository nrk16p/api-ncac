"""Pure rules: ADV approval by amount (menait-service spec 2026-09-29-finance-advance-v2-design.md §3),
two steps since v3 (spec 2026-10-05-finance-advance-v3-design.md §5).

No DB, no FastAPI. Inputs are plain dicts so everything is unit-testable.
Person: {"employee_id", "id", "level", "department_id", "active"}.
Tier:   {"clause", "amount_max" (Decimal | None = no cap), "min_level", "approver_label", "sort_order"}.
Step:   {"step", "required_level", "label"} — step 1 = หัวหน้าถัดไป, step 2 = ผู้มีอำนาจตาม TOA.
Log:    {"id", "level_no", "action", "action_by" (users.id), "action_at", "remark"} (form_approval_logs).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping, Optional

from services.finance.advance_logic import AdvanceRuleError

ORG_WIDE_LEVEL = 9
TAB_MINE = "mine"
TAB_DELEGABLE = "delegable"

STEP_LEADER = 1  # หัวหน้าถัดไป
STEP_TOA = 2     # ผู้มีอำนาจตาม TOA

OUTCOME_APPROVED = "approved"
OUTCOME_STEP = "step"

ACTION_APPROVED = "APPROVED"
ACTION_REJECTED = "REJECTED"
MARKER_RETURNED = "RETURNED"        # level_no 0, written by Finance (v3 §6)
MARKER_RESUBMITTED = "RESUBMITTED"  # level_no 0, written by the requester; starts a new approval round

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
    """The TOA level (v2 single-step rule) = the final step's level."""
    if requester_level is None:
        raise AdvanceRuleError(MSG_NO_LEVEL)
    return max(tier_min_level, min(requester_level + 1, ORG_WIDE_LEVEL))


def leader_level(requester_level: Optional[int]) -> int:
    """Step 1 (หัวหน้าถัดไป): the next level up, capped at the org-wide level."""
    if requester_level is None:
        raise AdvanceRuleError(MSG_NO_LEVEL)
    return min(requester_level + 1, ORG_WIDE_LEVEL)


def leader_label(level: int) -> str:
    return f"หัวหน้าระดับ {level} ขึ้นไป"


def toa_label(level: int, clause, approver_label) -> str:
    return f"ระดับ {level} ขึ้นไป (ข้อ {clause} · {approver_label})"


def plan_steps(requester_level: Optional[int], tier: Mapping) -> list:
    """Step 1 always; step 2 only when the TOA level is above step 1's level (min 1, max 2 steps)."""
    first = leader_level(requester_level)
    steps = [{"step": STEP_LEADER, "required_level": first, "label": leader_label(first)}]
    final = required_level(tier["min_level"], requester_level)
    if final > first:
        steps.append({"step": STEP_TOA, "required_level": final,
                      "label": toa_label(final, tier["clause"], tier["approver_label"])})
    return steps


def current_step_of(steps: list, current_level) -> int:
    """form_submissions.current_approval_level → a valid step number (clamped to the current plan)."""
    try:
        number = int(current_level)
    except (TypeError, ValueError):
        number = STEP_LEADER
    return min(max(number, STEP_LEADER), len(steps))


def skipped_steps(steps: list, current_step: int, approver_level: Optional[int]) -> list:
    """Later steps the approver's own level already satisfies (dynamic skip), up to the first one it doesn't."""
    skipped = []
    for spec in sorted(steps, key=lambda s: s["step"]):
        if spec["step"] <= current_step:
            continue
        if approver_level is None or approver_level < spec["required_level"]:
            break
        skipped.append(spec["step"])
    return skipped


def next_after_approval(steps: list, current_step: int, approver_level: Optional[int]) -> tuple:
    """('approved', None) when nothing is left after the skip, else ('step', the next step number)."""
    for spec in sorted(steps, key=lambda s: s["step"]):
        if spec["step"] <= current_step:
            continue
        if approver_level is None or approver_level < spec["required_level"]:
            return OUTCOME_STEP, spec["step"]
    return OUTCOME_APPROVED, None


def skip_remark(step: int, from_step: int, approver_level: int, required: int) -> str:
    return (f"ผ่านขั้น {step} พร้อมการอนุมัติขั้น {from_step}: "
            f"ผู้อนุมัติระดับ {approver_level} ถึงระดับ {required} ที่ขั้น {step} ต้องการ")


# ---------------------------- approval rounds (v3 §6) ----------------------------

def _sort_key(log: Mapping):
    return log.get("id") if log.get("id") is not None else 0


def current_round(logs: Iterable[Mapping]) -> list:
    """Logs after the latest RESUBMITTED marker, or all logs when there is none (ordered by id)."""
    ordered = sorted(logs, key=_sort_key)
    start = 0
    for index, log in enumerate(ordered):
        if log.get("action") == MARKER_RESUBMITTED:
            start = index + 1
    return ordered[start:]


def _is_step_approval(log: Mapping) -> bool:
    """Markers (level_no 0) are never approvals."""
    level = log.get("level_no")
    return log.get("action") == ACTION_APPROVED and level is not None and level >= STEP_LEADER


def excluded_approver(round_logs: Iterable[Mapping], step: int) -> frozenset:
    """users.id of whoever approved an earlier step in this round — they cannot approve `step` (empty at step 1)."""
    return frozenset(log.get("action_by") for log in round_logs
                     if _is_step_approval(log) and log["level_no"] < step and log.get("action_by") is not None)


def step_approvals(round_logs: Iterable[Mapping]) -> list:
    """The approval of each step in this round (earliest wins), ordered by step."""
    by_step = {}
    for log in sorted(round_logs, key=_sort_key):
        if _is_step_approval(log) and log["level_no"] not in by_step:
            by_step[log["level_no"]] = {"step": log["level_no"], "action_by": log.get("action_by"),
                                        "action_at": log.get("action_at")}
    return [by_step[step] for step in sorted(by_step)]


def leader_approved(round_logs: Iterable[Mapping], current_level=None) -> bool:
    """Step 1 is approved in this round, or the submission already sits past step 1 (ADV edit lock, v3 §6)."""
    if isinstance(current_level, int) and current_level > STEP_LEADER:
        return True
    return any(a["step"] == STEP_LEADER for a in step_approvals(round_logs))


# ---------------------------- eligibility ----------------------------

def can_approve(approver: Mapping, requester: Mapping, required: int, mapped_departments: Iterable[int],
                excluded: Iterable[int] = ()) -> bool:
    """`required` is the level of the step being approved; `excluded` holds users.id values (the earlier steps'
    approvers of the current round)."""
    if not approver.get("active"):
        return False
    level = approver.get("level")
    if level is None or level < required:
        return False
    if approver.get("employee_id") == requester.get("employee_id"):
        return False
    if approver.get("id") is not None and approver.get("id") in set(excluded):
        return False
    if level >= ORG_WIDE_LEVEL:
        return True
    dept = requester.get("department_id")
    if dept is None:
        return False
    return approver.get("department_id") == dept or dept in set(mapped_departments)


def _eligible(people: Iterable[Mapping], requester: Mapping, required: int,
              mappings: Mapping[str, Iterable[int]], excluded: Iterable[int]) -> list:
    excluded = frozenset(excluded)
    return [p for p in people
            if can_approve(p, requester, required, mappings.get(p["employee_id"], ()), excluded)]


def direct_level(people: Iterable[Mapping], requester: Mapping, required: int,
                 mappings: Mapping[str, Iterable[int]], excluded: Iterable[int] = ()) -> Optional[int]:
    levels = [p["level"] for p in _eligible(people, requester, required, mappings, excluded)]
    return min(levels) if levels else None


def direct_approvers(people: Iterable[Mapping], requester: Mapping, required: int,
                     mappings: Mapping[str, Iterable[int]], excluded: Iterable[int] = ()) -> list:
    eligible = _eligible(people, requester, required, mappings, excluded)
    if not eligible:
        return []
    lowest = min(p["level"] for p in eligible)
    return [p for p in eligible if p["level"] == lowest]


def approval_tab(approver_level: int, direct: Optional[int]) -> str:
    return TAB_MINE if direct is not None and approver_level <= direct else TAB_DELEGABLE


def evaluate(tiers, people: Mapping[str, Mapping], mappings, requester_employee_id: str, amount) -> dict:
    """The whole chain. `required_level` / `direct_level` are the final step's (the TOA, as in v2)."""
    tier = pick_tier(tiers, amount)
    requester = people.get(requester_employee_id)
    steps = plan_steps(requester.get("level") if requester else None, tier)
    required = steps[-1]["required_level"]
    direct = direct_level(people.values(), requester, required, mappings)
    if direct is None:  # the final step's approvers also qualify for step 1, so this covers every step
        raise AdvanceRuleError(MSG_NO_APPROVER)
    return {"clause": tier["clause"], "approver_label": tier["approver_label"], "min_level": tier["min_level"],
            "required_level": required, "direct_level": direct, "steps": steps}


def evaluate_step(evaluated: Mapping, people: Mapping[str, Mapping], mappings, requester_employee_id: str,
                  current_level, round_logs: Iterable[Mapping] = ()) -> dict:
    """The submission's current step: its level, label, excluded approvers and direct (lowest eligible) level."""
    steps = evaluated["steps"]
    step = current_step_of(steps, current_level)
    spec = steps[step - 1]
    excluded = excluded_approver(round_logs, step)
    requester = people.get(requester_employee_id)
    direct = (direct_level(people.values(), requester, spec["required_level"], mappings, excluded)
              if requester is not None else None)
    return {"step": step, "total_steps": len(steps), "required_level": spec["required_level"],
            "label": spec["label"], "direct_level": direct, "excluded": excluded}


def decide(evaluated: Mapping, people: Mapping[str, Mapping], mappings, requester_employee_id: str,
           approver_employee_id: str, current_level, round_logs: Iterable[Mapping] = ()) -> dict:
    """May this person act on the current step, and what happens if they approve it."""
    state = evaluate_step(evaluated, people, mappings, requester_employee_id, current_level, round_logs)
    approver = people.get(approver_employee_id)
    requester = people.get(requester_employee_id)
    allowed = (approver is not None and requester is not None
               and can_approve(approver, requester, state["required_level"],
                               mappings.get(approver_employee_id, ()), state["excluded"]))
    level = approver.get("level") if approver is not None else None
    steps = evaluated["steps"]
    outcome, next_step = next_after_approval(steps, state["step"], level)
    return {"allowed": allowed, "step": state["step"], "total_steps": state["total_steps"], "steps": steps,
            "approver_level": level, "outcome": outcome, "next_step": next_step,
            "skipped": skipped_steps(steps, state["step"], level)}
