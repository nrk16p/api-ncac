-- หน่วยนับบนใบสำคัญจ่าย แยกจากหน่วย SKU ของ ATMS (ใบจริงนับ คัน/เส้น/เที่ยว ไม่ใช่ "งาน")
-- เฉพาะฐานที่รัน seed/spec ไปก่อน 2026-10-13 ตอนเย็น · รันซ้ำได้
UPDATE fin_petty_expense_types SET spec = spec || '{"print": {"unit": "คัน"}}'::jsonb   WHERE code IN ('WEIGH', 'REPAIR');
UPDATE fin_petty_expense_types SET spec = spec || '{"print": {"unit": "เส้น"}}'::jsonb   WHERE code = 'TIRE';
UPDATE fin_petty_expense_types SET spec = spec || '{"print": {"unit": "เที่ยว"}}'::jsonb WHERE code IN ('TOLL', 'TRAVEL');
