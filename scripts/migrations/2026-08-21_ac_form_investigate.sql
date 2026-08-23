-- ============================================================
-- AC Form + AC Investigation Report — ฟิลด์ที่ FE ส่งมาแต่ DB ยังไม่มี
-- วันที่: 2026-08-21
--
-- Base.metadata.create_all() สร้างได้เฉพาะ "ตารางใหม่" — ไม่ ALTER ตารางเดิม
-- ดังนั้นต้องรันสคริปต์นี้กับ Postgres production ก่อน deploy โค้ดชุดใหม่
--
-- รันแบบ idempotent ได้ (IF NOT EXISTS ทุกคำสั่ง)
--   psql "$DATABASE_URL" -f scripts/migrations/2026-08-21_ac_form_investigate.sql
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1) accident_cases : เลขที่ใบแจ้งซ่อม + สถานะรถเสีย
-- ------------------------------------------------------------
ALTER TABLE accident_cases
    ADD COLUMN IF NOT EXISTS repair_request_no VARCHAR(50);

-- ค่าที่ FE ส่งมาเป็นข้อความไทย "ไม่สามารถวิ่งต่อได้" = 19 ตัวอักษร
-- VARCHAR(20) เหลือที่ว่างแค่ 1 ตัว เพิ่มตัวเลือกใหม่เมื่อไหร่ก็ 500 ทันที
ALTER TABLE accident_cases
    ADD COLUMN IF NOT EXISTS breakdown_status VARCHAR(50);

-- เผื่อกรณีเคยรันสคริปต์เวอร์ชันก่อนหน้าที่สร้างเป็น VARCHAR(20) ไว้แล้ว
ALTER TABLE accident_cases
    ALTER COLUMN breakdown_status TYPE VARCHAR(50);


-- ------------------------------------------------------------
-- 2) accident_case_damage_items : รายการความเสียหายรายบรรทัด
--    (สินค้า / รถและอื่นๆ — ยอดรวมไปลงที่ actual_goods / actual_vehicle)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS accident_case_damage_items (
    damage_item_id      SERIAL PRIMARY KEY,
    document_no_ac      VARCHAR(50) NOT NULL
                        REFERENCES accident_cases (document_no_ac) ON DELETE CASCADE,
    seq                 INTEGER NOT NULL DEFAULT 1,
    damage_category     VARCHAR(20),      -- goods | vehicle
    damage_detail       TEXT,
    damage_value        NUMERIC(12, 2) DEFAULT 0,
    responsible_party   VARCHAR(255)
);

CREATE INDEX IF NOT EXISTS ix_accident_case_damage_items_document_no_ac
    ON accident_case_damage_items (document_no_ac);


-- ------------------------------------------------------------
-- 3) accident_case_investigate_whys : ผูก WHY กับสาเหตุที่สังกัด
--    FE แยกชุด Why-Why ตามแต่ละสาเหตุ ถ้าไม่เก็บคอลัมน์นี้
--    WHY ของทุกสาเหตุจะยุบไปรวมที่สาเหตุแรกตอนโหลดฟอร์มใหม่
-- ------------------------------------------------------------
ALTER TABLE accident_case_investigate_whys
    ADD COLUMN IF NOT EXISTS root_cause_client_id VARCHAR(64);

CREATE INDEX IF NOT EXISTS ix_ac_inv_whys_root_cause
    ON accident_case_investigate_whys (investigate_ac_id, root_cause_client_id);


-- ------------------------------------------------------------
-- 4) accident_case_investigate_root_causes : ประเด็นตั้งต้นของชุดวิเคราะห์
--    (= problem ของ WHY1)
-- ------------------------------------------------------------
ALTER TABLE accident_case_investigate_root_causes
    ADD COLUMN IF NOT EXISTS problem TEXT;


-- ------------------------------------------------------------
-- 5) accident_case_investigate_investigators : รหัสพนักงาน
--    ใช้ผูกกับทะเบียนพนักงานแทนการเทียบจากชื่อ
-- ------------------------------------------------------------
ALTER TABLE accident_case_investigate_investigators
    ADD COLUMN IF NOT EXISTS employee_id VARCHAR(50);


COMMIT;

-- ============================================================
-- ตรวจผลหลังรัน
-- ============================================================
-- SELECT column_name, data_type
--   FROM information_schema.columns
--  WHERE table_name = 'accident_cases'
--    AND column_name IN ('repair_request_no', 'breakdown_status');
--
-- SELECT column_name FROM information_schema.columns
--  WHERE table_name = 'accident_case_damage_items' ORDER BY ordinal_position;
--
-- SELECT table_name, column_name FROM information_schema.columns
--  WHERE column_name IN ('root_cause_client_id', 'problem', 'employee_id')
--    AND table_name LIKE 'accident_case_investigate%';
