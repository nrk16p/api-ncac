"""ด่านตรวจของเบิกเงินสดย่อย — ล้วน ไม่ query เอง (repo ส่งข้อมูลที่ต้องใช้มาให้)

สองด่านคนละเรื่องกัน:
  เอกสารไม่ครบ = บล็อก ไม่มีทับ
  ซ้ำ          = เข้ม (บัตรชั่ง+เบอร์รถ+ทะเบียน) บล็อก · อ่อน (วันที่+รถ) เตือน ทับได้พร้อมเหตุผล
"""
from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Mapping

from services.finance import petty_logic as L

STAGE_APPROVE = "BEFORE_APPROVE"
STAGE_SUBMIT_ACC = "BEFORE_SUBMIT_TO_ACCOUNTING"

_FIELD_LABELS = {"dd_no": "เลขที่ใบรับสินค้า (DD)", "receipt_no": "เลขที่ใบเสร็จ"}


def _rule_applies(when: str, claim: Mapping) -> bool:
    if not when or when == "always":
        return True
    amount = Decimal(str(claim.get("amount") or 0))
    try:
        if when.startswith("total >="):
            return amount >= Decimal(when.split(">=")[1].strip())
        if when.startswith("total <"):
            return amount < Decimal(when.split("<")[1].strip())
    except Exception:
        return False
    return False  # เงื่อนไขที่ยังไม่รองรับ = ไม่บังคับ ดีกว่าบล็อกใบด้วยกฎที่อ่านไม่ออก


def _dedupe(rows: list[dict]) -> list[dict]:
    seen, out = set(), []
    for row in rows:
        token = (row.get("kind"), row.get("key"), row.get("line_no"))
        if token in seen:
            continue
        seen.add(token)
        out.append(row)
    return out


def validate_claim(claim: Mapping, items: list[Mapping], spec: Mapping, stage: str,
                   attachments: Mapping[str, int]) -> dict:
    """คืน {"missing": [...], "warnings": [...]} — missing ไม่ว่าง = ห้ามผ่านด่านนี้"""
    missing: list[dict] = []
    labels = {a["key"]: a.get("label", a["key"]) for a in spec.get("attachments", []) or []}

    if stage == STAGE_APPROVE:
        # ไฟล์แนบที่ spec บอกว่าบังคับ
        for att in spec.get("attachments", []) or []:
            if att.get("required") and not attachments.get(att["key"]):
                missing.append({"kind": "attachment", "key": att["key"],
                                "label": att.get("label", att["key"])})
        # ช่องบังคับของรายการ
        for field in spec.get("item_fields", []) or []:
            if not field.get("required"):
                continue
            key = field["key"]
            for index, item in enumerate(items, start=1):
                value = item.get(key)
                if value is None or str(value).strip() == "":
                    missing.append({"kind": "item_field", "key": key, "line_no": index,
                                    "label": f"{field.get('label', key)} (แถวที่ {index})"})

    # กฎเฉพาะของประเภท
    for rule in spec.get("rules", []) or []:
        if rule.get("at") != stage or not _rule_applies(rule.get("when", "always"), claim):
            continue
        field = rule.get("require_field")
        if field and not str(claim.get(field) or "").strip():
            missing.append({"kind": "field", "key": field, "label": _FIELD_LABELS.get(field, field)})
        att = rule.get("require_attachment")
        if att and not attachments.get(att):
            missing.append({"kind": "attachment", "key": att, "label": labels.get(att, att)})

    return {"missing": _dedupe(missing), "warnings": []}


def _soft_key(item: Mapping) -> tuple[str, str]:
    return (L.normalize_code(item.get("truck_code")), L.normalize_plate(item.get("truck_plate")))


def split_duplicates(items: Iterable[Mapping], prior: Iterable[Mapping], spec: Mapping) -> dict:
    """เทียบรายการในใบนี้กับใบอื่น *และกับกันเอง* — ของจริงมีรถซ้ำในใบเดียวมาแล้ว"""
    cfg = spec.get("duplicate_check") or {}
    soft_days = int((cfg.get("soft") or {}).get("days", 7))
    hard: list[dict] = []
    soft: list[dict] = []
    pool = list(prior)

    for index, item in enumerate(items, start=1):
        key = L.hard_dup_key(item)
        matched_hard = None
        if key is not None:
            matched_hard = next((o for o in pool if L.hard_dup_key(o) == key), None)
        if matched_hard is not None:
            hard.append({"line_no": index, "form_id": matched_hard.get("form_id"),
                         "ref_no": item.get("ref_no"), "truck_code": item.get("truck_code"),
                         "message": "บัตรชั่งใบนี้ของรถคันนี้เบิกไปแล้ว"})
        else:
            for other in pool:
                if _soft_key(other) != _soft_key(item):
                    continue
                here, there = item.get("event_date"), other.get("event_date")
                if here and there and abs((here - there).days) <= soft_days:
                    soft.append({"line_no": index, "form_id": other.get("form_id"),
                                 "event_date": str(here), "truck_code": item.get("truck_code"),
                                 "message": f"รถคันนี้มีรายการใน {soft_days} วันที่ผ่านมา"})
                    break
        pool.append(dict(item, form_id=item.get("form_id")))

    return {"hard": hard, "soft": soft}
