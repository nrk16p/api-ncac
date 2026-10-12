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

# spec เรียกรถว่าช่องเดียว แต่ตารางเก็บเป็นเบอร์รถ + ทะเบียน และผู้ใช้สั่งว่าด่านซ้ำต้องใช้ทั้งคู่
# ฉะนั้นช่อง truck ถือว่าครบเมื่อมีทั้งสองค่า
_ITEM_ALIASES = {"truck": ("truck_code", "truck_plate")}


def _item_filled(item: Mapping, key: str) -> bool:
    return all(str(item.get(k) or "").strip() != "" for k in _ITEM_ALIASES.get(key, (key,)))


def _field_value(claim: Mapping, key: str):
    """ค่าช่องหัวใบ: คอลัมน์ของใบก่อน แล้วค่อยดูใน detail (ช่องตาม spec.header_fields)"""
    if key in claim and claim.get(key) not in (None, ""):
        return claim.get(key)
    detail = claim.get("detail") or {}
    return detail.get(key) if isinstance(detail, Mapping) else None


def _rule_applies(when: str, claim: Mapping) -> bool:
    """เงื่อนไขที่รองรับ: always · total >= N · total < N · ช่อง == ค่า (ต่อกันด้วย &&)
    เช่น "payment_method == โอนเงิน && payee_type == บุคคล/ร้านเงินสด" (REQUIREMENT §3.3 บัตรประชาชน)
    เงื่อนไขที่อ่านไม่ออก = ไม่บังคับ ดีกว่าบล็อกใบด้วยกฎที่ผิด"""
    if not when or when == "always":
        return True
    amount = Decimal(str(claim.get("amount") or 0))
    try:
        if when.startswith("total >="):
            return amount >= Decimal(when.split(">=")[1].strip())
        if when.startswith("total <"):
            return amount < Decimal(when.split("<")[1].strip())
        if "==" in when:
            for clause in when.split("&&"):
                key, _, wanted = clause.partition("==")
                key, wanted = key.strip(), wanted.strip()
                if not key or not wanted:
                    return False
                if str(_field_value(claim, key) or "").strip() != wanted:
                    return False
            return True
    except Exception:
        return False
    return False


def _dedupe(rows: list[dict]) -> list[dict]:
    seen, out = set(), []
    for row in rows:
        token = (row.get("kind"), row.get("key"), row.get("line_no"))
        if token in seen:
            continue
        seen.add(token)
        out.append(row)
    return out


def missing_item_fields(items: list[Mapping], spec: Mapping) -> list[dict]:
    """ช่องบังคับของรายการที่ยังว่าง — ใช้ทั้งตอนเปิดใบ/แก้ใบ และซ้ำอีกครั้งในด่านอนุมัติ"""
    missing: list[dict] = []
    for field in spec.get("item_fields", []) or []:
        if not field.get("required"):
            continue
        key = field["key"]
        for index, item in enumerate(items, start=1):
            if not _item_filled(item, key):
                missing.append({"kind": "item_field", "key": key, "line_no": index,
                                "label": f"{field.get('label', key)} (แถวที่ {index})"})
    return missing


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
        missing.extend(missing_item_fields(items, spec))

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
                         "event_date": str(matched_hard.get("event_date") or ""),
                         "payee_name": matched_hard.get("payee_name"), "amount": matched_hard.get("amount"),
                         "message": ("เลขอ้างอิงนี้ของรถคันนี้เบิกไปแล้ว"
                                     if (item.get("truck_code") or item.get("truck_plate")) else "เลขอ้างอิงนี้เบิกไปแล้ว")})
        else:
            for other in pool:
                if _soft_key(other) != _soft_key(item):
                    continue
                here, there = item.get("event_date"), other.get("event_date")
                if here and there and abs((here - there).days) <= soft_days:
                    soft.append({"line_no": index, "form_id": other.get("form_id"),
                                 "event_date": str(there), "truck_code": item.get("truck_code"),
                                 "payee_name": other.get("payee_name"), "amount": other.get("amount"),
                                 "message": f"รถคันนี้มีรายการใน {soft_days} วันที่ผ่านมา"})
                    break
        pool.append(dict(item, form_id=item.get("form_id")))

    return {"hard": hard, "soft": soft}
