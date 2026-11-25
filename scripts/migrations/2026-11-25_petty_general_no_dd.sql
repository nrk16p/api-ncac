-- เงินสดย่อย 25/11/2026: ค่าใช้จ่ายทั่วไป (GENERAL) ยอดเกิน 500 ไม่ต้องมี DD ก่อนส่งเคลียร์ (ผู้ใช้สั่ง)
-- เอากติกา "BEFORE_SUBMIT_TO_ACCOUNTING total >= 500 require dd_no" ออกจาก spec ของ GENERAL ตัวเดียว
-- กติกาอื่นของ GENERAL และของประเภทอื่นไม่แตะ · ไม่ต้อง deploy โค้ด (BE/FE อ่านกติกาจาก spec) · รันซ้ำได้

BEGIN;

UPDATE fin_petty_expense_types
SET spec = jsonb_set(
      spec, '{rules}',
      COALESCE((
        SELECT jsonb_agg(r)
        FROM jsonb_array_elements(spec->'rules') AS r
        WHERE NOT (r->>'at' = 'BEFORE_SUBMIT_TO_ACCOUNTING' AND r->>'require_field' = 'dd_no')
      ), '[]'::jsonb)),
    updated_at = now(),
    updated_by = 'migration-2026-11-25'
WHERE code = 'GENERAL'
  AND EXISTS (
    SELECT 1 FROM jsonb_array_elements(spec->'rules') AS r
    WHERE r->>'at' = 'BEFORE_SUBMIT_TO_ACCOUNTING' AND r->>'require_field' = 'dd_no'
  );

COMMIT;

-- ตรวจหลังรัน (อ่านอย่างเดียว) — GENERAL ต้องไม่มีกติกา dd_no แล้ว ประเภทอื่นยังมีครบ:
-- SELECT code, spec->'rules' FROM fin_petty_expense_types ORDER BY code;
