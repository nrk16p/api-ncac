-- ผู้ใช้สั่ง 2026-10-13: ใบสรุปรายการต้องมี พจส. ต่อแถว
-- เฉพาะฐานที่ seed ไปก่อนแล้ว · รันซ้ำได้
ALTER TABLE fin_petty_claim_items ADD COLUMN IF NOT EXISTS driver_name varchar(150);

-- แทรกช่อง driver_name ไว้ถัดจากรถ: ค่าชั่ง/ยาง มีประเภทรถคั่น (ตำแหน่ง 3) · ซ่อมฉุกเฉินไม่มี (ตำแหน่ง 2)
UPDATE fin_petty_expense_types
SET spec = jsonb_insert(spec, '{item_fields, 3}', '{"key":"driver_name","label":"พจส.","type":"text","required":false}'::jsonb)
WHERE code IN ('WEIGH', 'TIRE') AND NOT (spec->'item_fields') @> '[{"key":"driver_name"}]'::jsonb;
UPDATE fin_petty_expense_types
SET spec = jsonb_insert(spec, '{item_fields, 2}', '{"key":"driver_name","label":"พจส.","type":"text","required":false}'::jsonb)
WHERE code = 'REPAIR' AND NOT (spec->'item_fields') @> '[{"key":"driver_name"}]'::jsonb;
