-- เงินสดย่อย: เพิ่มประเภท "ค่าใช้จ่ายทั่วไป" (GENERAL) — ผู้ใช้สั่ง 29/10/2026
--
-- เอกสารตามตารางของผู้ใช้:
--   บิลเงินสด/ใบกำกับภาษี/ใบเสร็จรับเงิน = บังคับ รับบิลจริงเท่านั้น
--   ใบรับรองแทนใบเสร็จรับเงิน = เฉพาะกรณีบิลเงินสดเป็นสำเนา (หัวใบเลือก "บิลเงินสดเป็น" ตัวจริง/สำเนา)
--     ปุ่มพิมพ์ขึ้นเฉพาะใบที่เลือกสำเนา (print_when) และต้องแนบใบที่เซ็นแล้วก่อนอนุมัติ
--   หลักฐานการโอน = บังคับ · DD = ยอดตั้งแต่ 500 บาทต้องใส่ก่อนส่งบัญชี
-- ที่เหลือเหมือนค่าซ่อมฉุกเฉิน (จ่ายให้ / วิธีจ่าย / ผู้รับเงิน) · ทุกแผนกเปิดใบได้
-- ด่านซ้ำเข้ม = เลขที่บิล + จ่ายให้ เพราะใบทั่วไปส่วนใหญ่ไม่มีรถ (BE ต้องเป็นรุ่นที่อ่าน duplicate_check.hard.keys)
--
-- รันซ้ำได้: มีแถวแล้วไม่ทับ (กันทับค่าที่แก้ไว้ในหน้า master)
-- คอมเมนต์คั่น spec ด้านล่าง (spec:CODE / /spec) มีไว้ให้ tests/finance/test_petty_general_type.py อ่านของจริง

INSERT INTO fin_petty_expense_types (code, name_th, atms_sku, sort_order, is_active, spec, updated_at, updated_by)
VALUES ('GENERAL', 'ค่าใช้จ่ายทั่วไป', NULL, 6, true,
-- spec:GENERAL
'{
  "item_based": true,
  "item_label": "รายการค่าใช้จ่าย",
  "header_fields": [
    {"key": "period_from", "label": "ตั้งแต่วันที่", "type": "date", "required": true, "auto": "min(item.event_date)"},
    {"key": "period_to", "label": "ถึงวันที่", "type": "date", "required": true, "auto": "max(item.event_date)"},
    {"key": "payee_name", "label": "จ่ายให้", "type": "text", "required": true},
    {"key": "payment_method", "label": "วิธีจ่าย", "type": "select", "options": ["โอนเงิน", "เงินสด"], "required": true},
    {"key": "payee_type", "label": "ผู้รับเงิน", "type": "select", "options": ["นิติบุคคล", "บุคคล/ร้านเงินสด"], "required": true},
    {"key": "bill_kind", "label": "บิลเงินสดเป็น", "type": "select", "options": ["ตัวจริง", "สำเนา"], "default": "ตัวจริง", "required": true}
  ],
  "item_fields": [
    {"key": "event_date", "label": "วันที่ตามบิล", "type": "date", "required": true},
    {"key": "ref_no", "label": "เลขที่บิล/ใบเสร็จ", "type": "text", "required": true},
    {"key": "remark", "label": "รายการ", "type": "text", "required": true},
    {"key": "qty", "label": "จำนวน", "type": "number", "required": true},
    {"key": "rate", "label": "ราคา/หน่วย", "type": "money", "required": true},
    {"key": "truck", "label": "รถ (ถ้ามี)", "type": "truck", "search": ["code", "plate"], "required": false}
  ],
  "attachments": [
    {"key": "receipt", "label": "บิลเงินสด/ใบกำกับภาษี/ใบเสร็จรับเงิน", "required": true, "max": 10,
     "note": "บิลจริงเท่านั้น สแกนหรือถ่ายแนบที่นี่ แล้วส่งตัวจริงไปกับใบปะหน้า"},
    {"key": "receipt_substitute", "label": "ใบรับรองแทนใบเสร็จรับเงิน (เซ็นแล้ว)", "required": false, "max": 3,
     "note": "เฉพาะกรณีบิลเงินสดเป็นสำเนา พิมพ์จากปุ่มพิมพ์ของใบนี้ เซ็นแล้วแนบกลับ"},
    {"key": "transfer", "label": "หลักฐานการโอน", "required": true, "max": 3}
  ],
  "rules": [
    {"when": "total >= 500", "require_field": "dd_no", "at": "BEFORE_SUBMIT_TO_ACCOUNTING"},
    {"when": "always", "require_attachment": "receipt", "at": "BEFORE_APPROVE"},
    {"when": "always", "require_attachment": "transfer", "at": "BEFORE_APPROVE"},
    {"when": "bill_kind == สำเนา", "require_attachment": "receipt_substitute", "at": "BEFORE_APPROVE"}
  ],
  "duplicate_check": {
    "scope": "item",
    "hard": {"keys": ["ref_no", "payee_name"], "days": 365, "action": "block", "override": false},
    "soft": {"keys": ["event_date", "truck"], "days": 7, "action": "warn", "override": "reason"}
  },
  "print": {"unit": "รายการ"},
  "prints": ["PETTY_VOUCHER", "RECEIPT_SUBSTITUTE", "DD"],
  "print_when": {"RECEIPT_SUBSTITUTE": "bill_kind == สำเนา"}
}'
-- /spec
::jsonb, now(), 'migration')
ON CONFLICT (code) DO NOTHING;
