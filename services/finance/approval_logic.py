"""Pure rules: ADV approval by amount (menait-service spec 2026-09-29-finance-advance-v2-design.md §3),
one step at the TOA level since 21/10/2026 (user: "ส่งอนุมัติสูงสุดตาม TOA").

No DB, no FastAPI. Inputs are plain dicts so everything is unit-testable.
Person: {"employee_id", "id", "level", "department_id", "division_id", "active"}.
Tier:   {"clause", "amount_max" (Decimal | None = no cap), "min_level", "approver_label", "sort_order"}.
Step:   {"step", "required_level", "label"} — one step: ผู้มีอำนาจตาม TOA.
Log:    {"id", "level_no", "action", "action_by" (users.id), "action_at", "remark"} (form_approval_logs).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping, Optional

from services.finance.advance_logic import AdvanceRuleError
from services.finance.permission_logic import is_superadmin

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


def toa_label(level: int, clause, approver_label) -> str:
    return f"ระดับ {level} ขึ้นไป (ข้อ {clause} · {approver_label})"


def plan_steps(requester_level: Optional[int], tier: Mapping) -> list:
    """ขั้นเดียวตาม TOA (ผู้ใช้สั่ง 21/10/2026 "ส่งอนุมัติสูงสุดตาม TOA" แทนกติกา 2 ขั้นของ 19/10/2026)

    ระดับที่ต้องการ = ระดับ TOA ของยอดนั้น แต่ต้องสูงกว่าผู้เบิกอย่างน้อยหนึ่งระดับ (เพดาน ORG_WIDE_LEVEL)
    ใช้ทั้งตอนขอเบิกและตอนอนุมัติเคลียร์ ใครระดับถึงขึ้นไปเปิดลิงก์แล้วกดอนุมัติได้
    """
    final = required_level(tier["min_level"], requester_level)
    return [{"step": STEP_LEADER, "required_level": final,
             "label": toa_label(final, tier["clause"], tier["approver_label"])}]


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

def can_approve(approver: Mapping, requester: Mapping, required: int, mapped_divisions: Iterable[int],
                excluded: Iterable[int] = (), widen_to_division: bool = False) -> bool:
    """`required` is the level of the step being approved; `excluded` holds users.id values (the earlier steps'
    approvers of the current round). `widen_to_division` also lets anyone in the requester's division (ฝ่าย)
    approve — see needs_division_fallback."""
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
    if approver.get("department_id") == dept:
        return True
    division = requester.get("division_id")
    if division is None:
        return False
    return division in set(mapped_divisions) or (widen_to_division and approver.get("division_id") == division)


def _in_department_scope(approver: Mapping, requester: Mapping, mapped_divisions: Iterable[int]) -> bool:
    if requester.get("department_id") is not None and approver.get("department_id") == requester["department_id"]:
        return True
    return requester.get("division_id") is not None and requester["division_id"] in set(mapped_divisions)


def needs_division_fallback(people: Iterable[Mapping], requester: Mapping, required: int,
                            mappings: Mapping[str, Iterable[int]], excluded: Iterable[int] = ()) -> bool:
    """กติกาเดียวกับฟอร์ม IT (25/10/2026): หาผู้อนุมัติในแผนกก่อน (รวมผู้ดูแลฝ่ายใน form_approver_divisions)
    ถ้าไม่มีใครเลย ค่อยขยายไปคนในฝ่ายเดียวกัน — ผู้บริหารระดับ ORG_WIDE_LEVEL ไม่นับว่าอยู่ในแผนก"""
    if requester is None or requester.get("division_id") is None:
        return False
    excluded = frozenset(excluded)
    return not any(_in_department_scope(p, requester, mappings.get(p["employee_id"], ()))
                   and can_approve(p, requester, required, mappings.get(p["employee_id"], ()), excluded)
                   for p in people)


def may_approve(people: Iterable[Mapping], approver: Mapping, requester: Mapping, required: int,
                mappings: Mapping[str, Iterable[int]], excluded: Iterable[int] = ()) -> bool:
    """can_approve with the department → division fallback decided from everyone in `people`."""
    widen = needs_division_fallback(people, requester, required, mappings, excluded)
    return can_approve(approver, requester, required, mappings.get(approver.get("employee_id"), ()), excluded,
                       widen_to_division=widen)


def _eligible(people: Iterable[Mapping], requester: Mapping, required: int,
              mappings: Mapping[str, Iterable[int]], excluded: Iterable[int]) -> list:
    people = list(people)
    excluded = frozenset(excluded)
    widen = needs_division_fallback(people, requester, required, mappings, excluded)
    return [p for p in people
            if can_approve(p, requester, required, mappings.get(p["employee_id"], ()), excluded,
                           widen_to_division=widen)]


def notify_approvers(eligible: Iterable[Mapping], required: int) -> list:
    """ใครควร "ได้รับอีเมล" (ผู้ใช้สั่ง 21/10/2026): เฉพาะคนระดับที่ TOA กำหนด
    = ชั้นต่ำสุดในกลุ่มที่อนุมัติได้ (ถ้าไม่มีใครระดับตรงพอดี ใช้ชั้นถัดขึ้นไปที่มีคน)
    คนระดับสูงกว่ายังกดอนุมัติได้เหมือนเดิมผ่านลิงก์ใบ แค่ไม่ได้อีเมล
    """
    people = [p for p in eligible if p.get("level") is not None and p["level"] >= required]
    if not people:
        return []
    lowest = min(p["level"] for p in people)
    return [p for p in people if p["level"] == lowest]


def eligible_approvers(people: Iterable[Mapping], requester: Mapping, required: int,
                       mappings: Mapping[str, Iterable[int]], excluded: Iterable[int] = ()) -> list:
    """Every person who may approve a step of `required` level (not only the lowest level)."""
    return _eligible(people, requester, required, mappings, excluded)


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


def superadmin_may_approve(approver_employee_id, requester_employee_id, requester) -> bool:
    """superadmin อนุมัติได้ทุกขั้นทุกใบ ยกเว้นใบของตัวเอง (ผู้ใช้สั่ง 20/10/2026)"""
    return (requester is not None and is_superadmin(approver_employee_id)
            and str(approver_employee_id) != str(requester_employee_id))


def decide(evaluated: Mapping, people: Mapping[str, Mapping], mappings, requester_employee_id: str,
           approver_employee_id: str, current_level, round_logs: Iterable[Mapping] = ()) -> dict:
    """May this person act on the current step, and what happens if they approve it."""
    state = evaluate_step(evaluated, people, mappings, requester_employee_id, current_level, round_logs)
    approver = people.get(approver_employee_id)
    requester = people.get(requester_employee_id)
    allowed = (approver is not None and requester is not None
               and may_approve(people.values(), approver, requester, state["required_level"],
                               mappings, state["excluded"]))
    level = approver.get("level") if approver is not None else None
    if not allowed and superadmin_may_approve(approver_employee_id, requester_employee_id, requester):
        # superadmin กดแทนได้ทุกขั้น แต่ผ่านแค่ขั้นนี้ ไม่ข้ามขั้นถัดไปด้วยระดับของตัวเอง
        allowed, level = True, state["required_level"]
    steps = evaluated["steps"]
    outcome, next_step = next_after_approval(steps, state["step"], level)
    return {"allowed": allowed, "step": state["step"], "total_steps": state["total_steps"], "steps": steps,
            "approver_level": level, "outcome": outcome, "next_step": next_step,
            "skipped": skipped_steps(steps, state["step"], level)}
