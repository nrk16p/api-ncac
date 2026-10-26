"""Pure rules: ADV approval by amount (menait-service spec 2026-09-29-finance-advance-v2-design.md §3),
one step per TOA level since 26/10/2026 (user: "ทุกขั้นตามขั้น TOA").

No DB, no FastAPI. Inputs are plain dicts so everything is unit-testable.
Person: {"employee_id", "id", "level", "department_id", "division_id", "active"}.
Tier:   {"clause", "amount_max" (Decimal | None = no cap), "min_level", "approver_label", "sort_order"}.
Step:   {"step", "required_level", "label"} — one per TOA level above the requester; last = ผู้มีอำนาจตาม TOA.
Log:    {"id", "level_no", "action", "action_by" (users.id), "action_at", "remark"} (form_approval_logs).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping, Optional

from services.finance.advance_logic import AdvanceRuleError
from services.finance.permission_logic import is_superadmin

ORG_WIDE_LEVEL = 9

# ขั้นพิเศษ (ผู้ใช้สั่ง 26/10/2026): ใบของแผนกบัญชี (6) ยอดเกิน 2,000 (ทุกข้อยกเว้น 6.7) ต้องผ่าน 690041 อีกขั้น
# แทรกตามระดับของเขา เช่น 610055 ขอ 1,500 หัวหน้า (ระดับ 3) กดจบ · ขอ 5,000 หัวหน้า → 690041
# ขอ 15,000 หัวหน้า → 690041 → ผู้จัดการ (ระดับ 5)
SPECIAL_STEPS = ({"department_id": 6, "except_clauses": ("6.7",), "employee_id": "690041"},)
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


def leader_label(level: int, approver_label) -> str:
    return f"หัวหน้าระดับ {level} ขึ้นไป ({approver_label})"


def plan_steps(requester_level: Optional[int], tier: Mapping, tiers: Iterable[Mapping]) -> list:
    """ไล่ทุกขั้นตามชั้น TOA (ผู้ใช้สั่ง 26/10/2026 "ทุกขั้นตามขั้น TOA" แทนขั้นเดียวของ 21/10/2026)

    ขั้นสุดท้าย = ระดับ TOA ของยอดนั้น แต่ต้องสูงกว่าผู้เบิกอย่างน้อยหนึ่งระดับ (เพดาน ORG_WIDE_LEVEL)
    ขั้นก่อนหน้า = ทุกชั้น TOA ใน `tiers` ที่สูงกว่าระดับผู้เบิกและต่ำกว่าขั้นสุดท้าย เรียงจากต่ำไปสูง
    เช่น ผู้เบิกระดับ 2 ขอ 60,000 → ระดับ 3 → 5 → 8 · ระดับ 3 ขอ 15,000 → ระดับ 5 ขั้นเดียว
    ใช้ทั้งตอนขอเบิกและตอนอนุมัติเคลียร์ คนระดับสูงกว่ากดแทนได้และข้ามขั้นที่ระดับตัวเองถึง
    """
    final = required_level(tier["min_level"], requester_level)
    by_level = {}
    for t in sorted(tiers, key=lambda t: t["sort_order"]):
        if requester_level < t["min_level"] < final:
            by_level.setdefault(t["min_level"], t)
    steps = [{"step": i, "required_level": level, "label": leader_label(level, by_level[level]["approver_label"])}
             for i, level in enumerate(sorted(by_level), start=STEP_LEADER)]
    steps.append({"step": len(steps) + 1, "required_level": final,
                  "label": toa_label(final, tier["clause"], tier["approver_label"])})
    return steps


def current_step_of(steps: list, current_level) -> int:
    """form_submissions.current_approval_level → a valid step number (clamped to the current plan)."""
    try:
        number = int(current_level)
    except (TypeError, ValueError):
        number = STEP_LEADER
    return min(max(number, STEP_LEADER), len(steps))


def _covers(spec: Mapping, approver_level: Optional[int], approver_id=None) -> bool:
    """Does this approval also satisfy `spec`? A named step (ขั้นพิเศษ) only when it is that person; else by level."""
    if spec.get("approver"):
        return approver_id is not None and str(approver_id) == str(spec["approver"])
    return approver_level is not None and approver_level >= spec["required_level"]


def skipped_steps(steps: list, current_step: int, approver_level: Optional[int], approver_id=None,
                  done: Iterable[int] = ()) -> list:
    """Later steps this approval also satisfies (dynamic skip) and that are not approved yet in this round (`done`).
    Steps climb by level, so a level skip only jumps over a ขั้นพิเศษ it does not cover: e.g. 3 → 690041 → 5
    approved at step 1 by a level-5 manager logs step 3 now, and step 2 still waits for 690041."""
    done = set(done)
    return [spec["step"] for spec in sorted(steps, key=lambda s: s["step"])
            if spec["step"] > current_step and spec["step"] not in done
            and _covers(spec, approver_level, approver_id)]


def next_after_approval(steps: list, current_step: int, approver_level: Optional[int], approver_id=None,
                        done: Iterable[int] = ()) -> tuple:
    """('approved', None) when nothing is left after this approval, else ('step', the next step number).
    A step already approved in this round (`done`, e.g. by an earlier skip) is not waited for again."""
    done = set(done)
    for spec in sorted(steps, key=lambda s: s["step"]):
        if spec["step"] <= current_step or spec["step"] in done:
            continue
        if not _covers(spec, approver_level, approver_id):
            return OUTCOME_STEP, spec["step"]
    return OUTCOME_APPROVED, None


def skip_remark(step: int, from_step: int, approver_level: int, required: int) -> str:
    return (f"ผ่านขั้น {step} พร้อมการอนุมัติขั้น {from_step}: "
            f"ผู้อนุมัติระดับ {approver_level} ถึงระดับ {required} ที่ขั้น {step} ต้องการ")


def skip_note(spec: Mapping, from_step: int, approver_level) -> str:
    """The log remark for a step passed by the approval of `from_step` (level skip, or the named person's own)."""
    if spec.get("approver"):
        return f"ผ่านขั้นพิเศษ (ขั้น {spec['step']}) พร้อมการอนุมัติขั้น {from_step}: เป็นผู้อนุมัติที่ขั้นพิเศษกำหนด"
    return skip_remark(spec["step"], from_step, approver_level, spec["required_level"])


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
                excluded: Iterable[int] = (), widen_to_division: bool = False, named: Optional[str] = None) -> bool:
    """`required` is the level of the step being approved; `excluded` holds users.id values (the earlier steps'
    approvers of the current round). `widen_to_division` also lets anyone in the requester's division (ฝ่าย)
    approve — see needs_division_fallback. `named` = ขั้นพิเศษ: only that employee, whatever the level."""
    if not approver.get("active"):
        return False
    if named is not None:
        return (str(approver.get("employee_id")) == str(named)
                and approver.get("employee_id") != requester.get("employee_id")
                and not (approver.get("id") is not None and approver.get("id") in set(excluded)))
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


def _in_own_department(approver: Mapping, requester: Mapping) -> bool:
    return requester.get("department_id") is not None and approver.get("department_id") == requester["department_id"]


def needs_division_fallback(people: Iterable[Mapping], requester: Mapping, required: int,
                            mappings: Mapping[str, Iterable[int]], excluded: Iterable[int] = (),
                            named: Optional[str] = None) -> bool:
    """หาผู้อนุมัติในแผนกของผู้เบิกก่อน ถ้าในแผนกไม่มีใครอนุมัติได้เลย ค่อยขยายไปคนในฝ่ายเดียวกัน (25/10/2026)

    นับเฉพาะคนในแผนกของผู้เบิกจริง ๆ — ผู้ดูแลฝ่าย (form_approver_divisions) กับผู้บริหารระดับ ORG_WIDE_LEVEL
    ต่างแผนกไม่นับ (26/10/2026: ผู้ดูแลฝ่ายระดับ 7–8 ครอบเกือบทุกฝ่าย ถ้านับ fallback ไม่เกิดเลย
    ใบยอดเล็กจึงข้ามหัวหน้าในฝ่ายไปหาผู้บริหาร 168 กรณีจากการลองกับข้อมูลจริง เทียบ 45 กรณีก่อนย้ายแผนก)
    """
    if named is not None or requester is None or requester.get("division_id") is None:
        return False
    excluded = frozenset(excluded)
    return not any(_in_own_department(p, requester)
                   and can_approve(p, requester, required, mappings.get(p["employee_id"], ()), excluded)
                   for p in people)


def may_approve(people: Iterable[Mapping], approver: Mapping, requester: Mapping, required: int,
                mappings: Mapping[str, Iterable[int]], excluded: Iterable[int] = (),
                named: Optional[str] = None) -> bool:
    """can_approve with the department → division fallback decided from everyone in `people`."""
    widen = needs_division_fallback(people, requester, required, mappings, excluded, named)
    return can_approve(approver, requester, required, mappings.get(approver.get("employee_id"), ()), excluded,
                       widen_to_division=widen, named=named)


def _eligible(people: Iterable[Mapping], requester: Mapping, required: int,
              mappings: Mapping[str, Iterable[int]], excluded: Iterable[int], named: Optional[str] = None) -> list:
    people = list(people)
    excluded = frozenset(excluded)
    widen = needs_division_fallback(people, requester, required, mappings, excluded, named)
    return [p for p in people
            if can_approve(p, requester, required, mappings.get(p["employee_id"], ()), excluded,
                           widen_to_division=widen, named=named)]


def notify_approvers(eligible: Iterable[Mapping], required: int, requester: Optional[Mapping] = None,
                     named: Optional[str] = None) -> list:
    """ใครควร "ได้รับอีเมล" (ผู้ใช้สั่ง 21/10/2026): เฉพาะคนระดับที่ TOA กำหนด
    = ชั้นต่ำสุดในกลุ่มที่อนุมัติได้ (ถ้าไม่มีใครระดับตรงพอดี ใช้ชั้นถัดขึ้นไปที่มีคน)
    คนระดับสูงกว่ายังกดอนุมัติได้เหมือนเดิมผ่านลิงก์ใบ แค่ไม่ได้อีเมล

    สาขาก่อน (ผู้ใช้สั่ง 26/10/2026): ถ้าสาขาของผู้เบิก (site_id) มีคนอนุมัติได้ ส่งเฉพาะคนสาขานั้น
    (ชั้นต่ำสุดในสาขา) ถ้าสาขานั้นไม่มีใครเลย ค่อยส่งตามชั้นต่ำสุดจากทุกสาขา · สิทธิ์กดอนุมัติไม่เปลี่ยน
    """
    if named is not None:  # ขั้นพิเศษ: อีเมลถึงคนที่กำหนดคนเดียว
        return [p for p in eligible if str(p.get("employee_id")) == str(named)]
    people = [p for p in eligible if p.get("level") is not None and p["level"] >= required]
    if not people:
        return []
    site = requester.get("site_id") if requester else None
    same_site = [p for p in people if site is not None and p.get("site_id") == site]
    people = same_site or people
    lowest = min(p["level"] for p in people)
    return [p for p in people if p["level"] == lowest]


def eligible_approvers(people: Iterable[Mapping], requester: Mapping, required: int,
                       mappings: Mapping[str, Iterable[int]], excluded: Iterable[int] = (),
                       named: Optional[str] = None) -> list:
    """Every person who may approve a step of `required` level (not only the lowest level)."""
    return _eligible(people, requester, required, mappings, excluded, named)


def direct_level(people: Iterable[Mapping], requester: Mapping, required: int,
                 mappings: Mapping[str, Iterable[int]], excluded: Iterable[int] = (),
                 named: Optional[str] = None) -> Optional[int]:
    levels = [p["level"] for p in _eligible(people, requester, required, mappings, excluded, named)
              if p.get("level") is not None]
    return min(levels) if levels else None


def direct_approvers(people: Iterable[Mapping], requester: Mapping, required: int,
                     mappings: Mapping[str, Iterable[int]], excluded: Iterable[int] = (),
                     named: Optional[str] = None) -> list:
    eligible = [p for p in _eligible(people, requester, required, mappings, excluded, named)
                if p.get("level") is not None]
    if not eligible:
        return []
    lowest = min(p["level"] for p in eligible)
    return [p for p in eligible if p["level"] == lowest]


def approval_tab(approver_level: int, direct: Optional[int]) -> str:
    return TAB_MINE if direct is not None and approver_level <= direct else TAB_DELEGABLE


def special_step_approver(requester: Optional[Mapping], tier: Mapping, people: Mapping[str, Mapping]) -> Optional[str]:
    """employee_id ของขั้นพิเศษที่ใบนี้ต้องผ่าน (SPECIAL_STEPS) — ไม่มีเมื่อเป็นใบของคนนั้นเอง หรือคนนั้นไม่ active"""
    if requester is None:
        return None
    for rule in SPECIAL_STEPS:
        if requester.get("department_id") == rule["department_id"] and tier["clause"] not in rule["except_clauses"]:
            who = people.get(rule["employee_id"])
            if who is not None and who.get("active") and rule["employee_id"] != requester.get("employee_id"):
                return rule["employee_id"]
    return None


def special_label(employee_id) -> str:
    return f"ขั้นพิเศษแผนกบัญชี (รหัส {employee_id})"


def evaluate(tiers, people: Mapping[str, Mapping], mappings, requester_employee_id: str, amount) -> dict:
    """The whole chain. `required_level` / `direct_level` are the final step's (the TOA, as in v2)."""
    tier = pick_tier(tiers, amount)
    requester = people.get(requester_employee_id)
    steps = plan_steps(requester.get("level") if requester else None, tier, tiers)
    required = steps[-1]["required_level"]
    direct = direct_level(people.values(), requester, required, mappings)
    if direct is None:  # the final step's approvers also qualify for step 1, so this covers every step
        raise AdvanceRuleError(MSG_NO_APPROVER)
    special = special_step_approver(requester, tier, people)
    if special is not None:  # แทรกตามระดับของคนที่กำหนด: หลังขั้นที่ระดับไม่เกินเขา ก่อนขั้นที่สูงกว่า
        level = people[special].get("level") or 0
        at = next((i for i, s in enumerate(steps) if s["required_level"] > level), len(steps))
        named_step = {"required_level": level, "label": special_label(special), "approver": special}
        steps = [{**s, "step": i} for i, s in enumerate(steps[:at] + [named_step] + steps[at:], start=STEP_LEADER)]
    return {"clause": tier["clause"], "approver_label": tier["approver_label"], "min_level": tier["min_level"],
            "required_level": required, "direct_level": direct, "steps": steps}


def evaluate_step(evaluated: Mapping, people: Mapping[str, Mapping], mappings, requester_employee_id: str,
                  current_level, round_logs: Iterable[Mapping] = ()) -> dict:
    """The submission's current step: its level, label, excluded approvers and direct (lowest eligible) level."""
    steps = evaluated["steps"]
    step = current_step_of(steps, current_level)
    spec = steps[step - 1]
    named = spec.get("approver")
    excluded = excluded_approver(round_logs, step)
    requester = people.get(requester_employee_id)
    direct = (direct_level(people.values(), requester, spec["required_level"], mappings, excluded, named)
              if requester is not None else None)
    return {"step": step, "total_steps": len(steps), "required_level": spec["required_level"],
            "label": spec["label"], "direct_level": direct, "excluded": excluded, "approver": named}


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
                               mappings, state["excluded"], state["approver"]))
    level = approver.get("level") if approver is not None else None
    who = approver_employee_id
    if not allowed and superadmin_may_approve(approver_employee_id, requester_employee_id, requester):
        # superadmin กดแทนได้ทุกขั้น แต่ผ่านแค่ขั้นนี้ ไม่ข้ามขั้นถัดไปด้วยระดับหรือตัวตนของตัวเอง
        allowed, level, who = True, state["required_level"], None
    steps = evaluated["steps"]
    done = {a["step"] for a in step_approvals(round_logs)}
    outcome, next_step = next_after_approval(steps, state["step"], level, who, done)
    return {"allowed": allowed, "step": state["step"], "total_steps": state["total_steps"], "steps": steps,
            "approver_level": level, "outcome": outcome, "next_step": next_step,
            "skipped": skipped_steps(steps, state["step"], level, who, done)}
