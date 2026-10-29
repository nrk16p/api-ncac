-- เงินสดย่อย: พจส./พจร. · ตำแหน่งยาง · เลขที่ใบรับสภาพหนี้ (ผู้ใช้สั่ง 2026-10-29)
--
-- 1. ใบที่กรอกเป็นแถว (ทุกประเภทที่มีช่อง driver_name): ทุกแถวเลือก พจส. หรือ พจร. (ค่าตั้งต้น พจส.)
--    ใบเดี่ยว: ค่าทางด่วนเลือกที่หัวใบคู่กับชื่อ · ค่าเดินทางเลือกได้ว่าผู้เดินทางเป็น พจส./พจร. (ไม่บังคับ)
-- 2. ใบยาง: ตำแหน่งยางต่อแถว เลือกได้หลายตำแหน่ง รหัสชุดเดียวกับผังล้อ WMS (F1–F4 · RA1–RA8 · RB1–RB13)
-- 3. มี พจร. ในใบ (ทุกประเภท) → เจ้าของเงินสดต้องใส่เลขที่ใบรับสภาพหนี้ตอนอนุมัติ
-- 4. ตัวเลือกผู้รับเงิน "ร้านจดทะเบียน" เปลี่ยนชื่อเป็น "นิติบุคคล" (ทั้งใน spec และใบที่เปิดไว้แล้ว)
--
-- ต้องรัน **ก่อน** deploy BE ที่อ่านคอลัมน์ใหม่ · รันซ้ำได้ (ช่องที่มีแล้วไม่แทรกซ้ำ)
-- รันหลัง 2026-10-29_petty_flow_v2.sql

BEGIN;

ALTER TABLE fin_petty_claim_items ADD COLUMN IF NOT EXISTS driver_role   varchar(10);
ALTER TABLE fin_petty_claim_items ADD COLUMN IF NOT EXISTS tire_position varchar(120);
ALTER TABLE fin_petty_claims      ADD COLUMN IF NOT EXISTS debt_doc_no   varchar(50);

-- แทรกช่องหนึ่งช่องก่อน/หลังช่องที่มี key = anchor ในอาร์เรย์ช่องของ spec (ฟังก์ชันชั่วคราว หายเมื่อปิด session)
CREATE OR REPLACE FUNCTION pg_temp.petty_insert_field(arr jsonb, anchor text, field jsonb, after_anchor boolean)
RETURNS jsonb LANGUAGE sql AS $$
  SELECT COALESCE(jsonb_agg(x.f ORDER BY x.ord, x.sub), '[]'::jsonb) FROM (
    SELECT e.ord, 1 AS sub, e.f FROM jsonb_array_elements(arr) WITH ORDINALITY AS e(f, ord)
    UNION ALL
    SELECT e.ord, CASE WHEN after_anchor THEN 2 ELSE 0 END, field
      FROM jsonb_array_elements(arr) WITH ORDINALITY AS e(f, ord) WHERE e.f->>'key' = anchor
  ) x
$$;

-- เปลี่ยนป้ายของช่องที่มี key ตรง (ค่าอื่นในช่องคงเดิม)
CREATE OR REPLACE FUNCTION pg_temp.petty_relabel(arr jsonb, target text, label text)
RETURNS jsonb LANGUAGE sql AS $$
  SELECT COALESCE(jsonb_agg(CASE WHEN e.f->>'key' = target THEN e.f || jsonb_build_object('label', label) ELSE e.f END
                            ORDER BY e.ord), '[]'::jsonb)
  FROM jsonb_array_elements(arr) WITH ORDINALITY AS e(f, ord)
$$;

-- 1a) ใบแบบรายการ: พจส./พจร. ก่อนช่องชื่อ ทุกประเภทที่มี driver_name ในรายการ
UPDATE fin_petty_expense_types
SET spec = jsonb_set(spec, '{item_fields}', pg_temp.petty_relabel(
      pg_temp.petty_insert_field(spec->'item_fields', 'driver_name',
        '{"key":"driver_role","label":"พจส./พจร.","type":"select","options":["พจส.","พจร."],"required":true,"default":"พจส."}'::jsonb,
        false),
      'driver_name', 'ชื่อ พจส./พจร.')),
    updated_at = now()
WHERE (spec->'item_fields') @> '[{"key":"driver_name"}]'
  AND NOT (spec->'item_fields') @> '[{"key":"driver_role"}]';

-- 1b) ใบเดี่ยวที่มีช่องคนขับที่หัวใบ (ค่าทางด่วน): พจส./พจร. ก่อนช่องชื่อ
UPDATE fin_petty_expense_types
SET spec = jsonb_set(spec, '{header_fields}', pg_temp.petty_relabel(
      pg_temp.petty_insert_field(spec->'header_fields', 'driver',
        '{"key":"driver_role","label":"พจส./พจร.","type":"select","options":["พจส.","พจร."],"required":true,"default":"พจส."}'::jsonb,
        false),
      'driver', 'ชื่อ พจส./พจร.')),
    updated_at = now()
WHERE (spec->'header_fields') @> '[{"key":"driver"}]'
  AND NOT (spec->'header_fields') @> '[{"key":"driver_role"}]';

-- 1c) ค่าเดินทาง: ผู้เดินทางอาจเป็นพนักงานทั่วไป จึงไม่บังคับ · เลือก พจร. เมื่อไรต้องมีใบรับสภาพหนี้
UPDATE fin_petty_expense_types
SET spec = jsonb_set(spec, '{header_fields}', pg_temp.petty_insert_field(spec->'header_fields', 'payee_name',
      '{"key":"driver_role","label":"ผู้เดินทางเป็น พจส./พจร. (ถ้าใช่)","type":"select","options":["พจส.","พจร."],"required":false}'::jsonb,
      true)),
    updated_at = now()
WHERE code = 'TRAVEL'
  AND NOT (spec->'header_fields') @> '[{"key":"driver_role"}]';

-- 2) ใบยาง: ตำแหน่งยางต่อจาก "รายการที่ทำ"
UPDATE fin_petty_expense_types
SET spec = jsonb_set(spec, '{item_fields}', pg_temp.petty_insert_field(spec->'item_fields', 'remark',
      '{"key":"tire_position","label":"ตำแหน่งยาง","type":"tire_positions","required":true}'::jsonb, true)),
    updated_at = now()
WHERE code = 'TIRE'
  AND (spec->'item_fields') @> '[{"key":"remark"}]'
  AND NOT (spec->'item_fields') @> '[{"key":"tire_position"}]';

-- 4) ผู้รับเงิน: "ร้านจดทะเบียน" → "นิติบุคคล" (ผู้ใช้สั่ง 29/10) · กติกาบัตรประชาชนอิง "บุคคล/ร้านเงินสด" จึงไม่กระทบ
UPDATE fin_petty_expense_types
SET spec = jsonb_set(spec, '{header_fields}', (
      SELECT jsonb_agg(CASE WHEN e.f->>'key' = 'payee_type'
               THEN jsonb_set(e.f, '{options}', (
                      SELECT jsonb_agg(CASE WHEN o #>> '{}' = 'ร้านจดทะเบียน' THEN to_jsonb('นิติบุคคล'::text) ELSE o END
                                       ORDER BY oi)
                      FROM jsonb_array_elements(e.f->'options') WITH ORDINALITY AS x(o, oi)))
               ELSE e.f END ORDER BY e.ord)
      FROM jsonb_array_elements(spec->'header_fields') WITH ORDINALITY AS e(f, ord))),
    updated_at = now()
WHERE (spec->'header_fields') @> '[{"key":"payee_type","options":["ร้านจดทะเบียน"]}]';

UPDATE fin_petty_claims
SET detail = jsonb_set(detail, '{payee_type}', to_jsonb('นิติบุคคล'::text)), updated_at = now()
WHERE detail->>'payee_type' = 'ร้านจดทะเบียน';

COMMIT;

-- ตรวจหลังรัน (อ่านอย่างเดียว)
-- SELECT code, jsonb_path_query_array(spec, '$.item_fields[*].key') AS items,
--        jsonb_path_query_array(spec, '$.header_fields[*].key') AS header
-- FROM fin_petty_expense_types ORDER BY sort_order;
