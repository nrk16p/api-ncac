-- =====================================================================
-- complaint_master + driver_complaints.problem : text -> master id
-- 13 ส.ค. 2026
--
-- ทำไมต้องมีสคริปต์นี้:
--   main.py เรียก Base.metadata.create_all() ตอนสตาร์ท ซึ่ง **สร้างตารางใหม่
--   ให้เท่านั้น ไม่เคยแก้ตารางที่มีอยู่แล้ว** ทั้งการเปลี่ยนชนิดคอลัมน์
--   driver_complaints.problem และการแก้สคีมาของ complaint_master รุ่นเก่า
--   จึงต้องรันมือ
--
-- ลำดับที่ปลอดภัย: รันสคริปต์นี้ก่อน แล้วค่อย deploy โค้ด
--   (โค้ดขึ้นก่อนโดยที่ปลายทางยังเป็นสคีมาเก่า = 500 ทั้ง /complaints/
--    และ /complaint-masters/ ซึ่งเกิดขึ้นจริงมาแล้ววันนี้)
--
-- ทั้งไฟล์รันซ้ำได้ (idempotent) และเก็บข้อความเดิมของ problem ไว้ที่
-- problem_legacy เผื่อต้องย้อนกลับ — อย่าเพิ่งลบคอลัมน์นั้นจนกว่าจะมั่นใจ
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 0) ทิ้ง complaint_master รุ่นก่อนหน้า ถ้าเป็นคนละสคีมา
--
-- ในฐานจริงมีตารางชื่อเดียวกันอยู่ก่อนแล้ว แต่เป็นแบบ `type` / `values`
-- (สร้างไว้ 13 ส.ค. 2026 มี 2 แถว ทั้งคู่เป็นประเภทของหน่วยงาน 3 ซึ่งอยู่ใน
-- ชุด seed ข้างล่างครบทั้งคู่ จึงไม่มีข้อมูลอะไรหายไปจริง)
--
-- **เงื่อนไขคือ “ไม่มีคอลัมน์ name”** ไม่ใช่ drop ทิ้งดื้อ ๆ — รันซ้ำกับตาราง
-- รุ่นใหม่จะไม่ทำอะไรเลย ประเภทที่ผู้ใช้เพิ่มเองไว้จึงไม่หาย
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF to_regclass('public.complaint_master') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_name = 'complaint_master' AND column_name = 'name'
       )
    THEN
        RAISE NOTICE 'พบ complaint_master สคีมาเก่า (type/values) — ทิ้งแล้วสร้างใหม่';
        DROP TABLE public.complaint_master CASCADE;
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- 1) ตารางประเภทเรื่อง
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS complaint_master (
    id            serial PRIMARY KEY,
    department_id integer      NOT NULL REFERENCES departments(department_id),
    name          varchar(255) NOT NULL,
    icon          varchar(50),
    sort_order    integer      NOT NULL DEFAULT 0,
    is_active     boolean      NOT NULL DEFAULT true,
    created_at    timestamp    DEFAULT now(),
    updated_at    timestamp    DEFAULT now(),
    CONSTRAINT uq_complaint_master_dept_name UNIQUE (department_id, name)
);

CREATE INDEX IF NOT EXISTS ix_complaint_master_department_id
    ON complaint_master (department_id);

-- ---------------------------------------------------------------------
-- 2) ข้อมูลตั้งต้น — ยกมาจาก complaintTypesByDepartment ใน
--    hr-service/lib/data.ts ที่ถูกลบทิ้งไปแล้ว
--
--    `icon` เก็บ **ชื่อไอคอนของ lucide-react** ฝั่งหน้าจอ map ชื่อเป็น
--    คอมโพเนนต์เอง (lib/complaint-icons.ts) — backend ไม่ต้องรู้จักชุดไอคอน
--
--    ⚠️ ชื่อประเภทต้องตรงกับข้อความเดิมใน driver_complaints.problem เป๊ะ ๆ
--    (รวมช่องว่างรอบ "/" และ **ขีดกลางยาว –** ใน "การเบิก–จ่ายน้ำมัน")
--    ไม่งั้นขั้นที่ 3 จะจับคู่ข้อมูลเก่าไม่ได้
-- ---------------------------------------------------------------------
INSERT INTO complaint_master (department_id, name, icon, sort_order) VALUES
    -- 3 · ยานยนต์
    (3,  'สภาพรถ / ความพร้อมใช้งาน',            'Car',            1),
    (3,  'การซ่อมบำรุง / อุปกรณ์',               'Wrench',         2),
    (3,  'รถไม่ปลอดภัย / ชำรุด',                 'TriangleAlert',  3),
    (3,  'การจัดรถ',                            'CalendarRange',  4),

    -- 11 · Operation Support (เชื้อเพลิง)
    (11, 'การเบิก–จ่ายน้ำมัน',                   'Receipt',        1),
    (11, 'ปริมาณน้ำมันไม่ถูกต้อง',                'Fuel',           2),
    (11, 'ระบบบันทึกน้ำมัน',                     'Database',       3),
    (11, 'โปร / สิทธิประโยชน์น้ำมัน',             'Gift',           4),

    -- 19 · จัดส่งสระบุรี
    (19, 'แผนงาน / ตารางวิ่ง',                   'CalendarRange',  1),
    (19, 'เส้นทาง / โหลดงาน',                    'Route',          2),
    (19, 'เวลาทำงาน / OT',                       'Timer',          3),
    (19, 'การสั่งงานไม่เหมาะสม',                  'ClipboardX',     4),

    -- 15 · จัดส่งลาดกระบัง
    (15, 'แผนงาน / ตารางวิ่ง',                   'CalendarRange',  1),
    (15, 'เส้นทาง / โหลดงาน',                    'Route',          2),
    (15, 'เวลาทำงาน / OT',                       'Timer',          3),
    (15, 'การสั่งงานไม่เหมาะสม',                  'ClipboardX',     4),

    -- 20 · จัดส่งบางปะกง
    (20, 'แผนงาน / ตารางวิ่ง',                   'CalendarRange',  1),
    (20, 'เส้นทาง / โหลดงาน',                    'Route',          2),
    (20, 'เวลาทำงาน / OT',                       'Timer',          3),
    (20, 'การสั่งงานไม่เหมาะสม',                  'ClipboardX',     4),

    -- 8 · มาตรฐานความปลอดภัย
    (8,  'อุบัติเหตุ / เกือบเกิดอุบัติเหตุ',        'TriangleAlert',  1),
    (8,  'อุปกรณ์ PPE',                          'HardHat',        2),
    (8,  'สภาพแวดล้อมเสี่ยง',                     'CircleAlert',    3),
    (8,  'การฝ่าฝืนกฎความปลอดภัย',                'Ban',            4),

    -- 17 · Compliance
    (17, 'การปฏิบัติงานไม่เป็นไปตามข้อกำหนด',      'ClipboardX',     1),
    (17, 'พฤติกรรมและมารยาท',                    'BookUser',       2),
    (17, 'การส่งมอบงาน',                         'Truck',          3),
    (17, 'การดูแลรถและอุปกรณ์',                   'Wrench',         4),

    -- 24 · ทรัพยากรบุคคล
    (24, 'ค่าจ้าง / สวัสดิการ',                   'Wallet',         1),
    (24, 'วินัย / พฤติกรรม',                      'Scale',          2),
    (24, 'ความเป็นธรรมในการทำงาน',                'Scale',          3),
    (24, 'การคุกคาม / เลือกปฏิบัติ',               'UserX',          4),
    (24, 'ระเบียบ / นโยบาย',                      'BookOpen',       5)
ON CONFLICT ON CONSTRAINT uq_complaint_master_dept_name DO NOTHING;

-- ---------------------------------------------------------------------
-- 3) driver_complaints.problem : text -> integer FK
--
--    ทำเป็นคอลัมน์ใหม่แล้วสลับ ไม่ใช่ ALTER COLUMN ... USING เพราะ USING
--    ห้ามมี subquery จึงหาค่า id จากตาราง master ในนั้นไม่ได้
-- ---------------------------------------------------------------------
DO $$
DECLARE
    col_type  text;
    unmatched integer;
BEGIN
    SELECT data_type INTO col_type
    FROM information_schema.columns
    WHERE table_name = 'driver_complaints'
      AND column_name = 'problem';

    IF col_type IS NULL THEN
        RAISE EXCEPTION 'driver_complaints.problem ไม่มีอยู่ — ตรวจชื่อตารางก่อน';
    END IF;

    IF col_type = 'integer' THEN
        RAISE NOTICE 'problem เป็น integer อยู่แล้ว — ข้ามขั้นตอนแปลงข้อมูล';
        RETURN;
    END IF;

    -- 3.1 สำรองข้อความเดิมไว้ก่อนแตะอะไรทั้งนั้น
    ALTER TABLE driver_complaints ADD COLUMN IF NOT EXISTS problem_legacy text;
    UPDATE driver_complaints SET problem_legacy = problem;

    -- 3.2 จับคู่ข้อความเดิมกับ master ของหน่วยงานเดียวกัน
    ALTER TABLE driver_complaints ADD COLUMN problem_new integer;

    UPDATE driver_complaints d
    SET    problem_new = m.id
    FROM   complaint_master m
    WHERE  btrim(d.problem) = m.name
      AND  m.department_id  = d.department_id;

    -- 3.3 เตือนถ้ามีแถวที่จับคู่ไม่ได้ — ข้อความเดิมยังอยู่ที่ problem_legacy
    --     (ไม่ยกเลิก transaction เพราะแถวพวกนี้กลายเป็น "ยังไม่จัดประเภท"
    --      ซึ่งเป็นสถานะที่ระบบรองรับอยู่แล้ว ไม่ใช่ข้อมูลเสีย)
    SELECT count(*) INTO unmatched
    FROM   driver_complaints
    WHERE  problem_legacy IS NOT NULL
      AND  btrim(problem_legacy) <> ''
      AND  problem_new IS NULL;

    IF unmatched > 0 THEN
        RAISE NOTICE 'จับคู่ประเภทเรื่องไม่ได้ % แถว — ดูรายการท้ายไฟล์', unmatched;
    END IF;

    -- 3.4 สลับคอลัมน์
    ALTER TABLE driver_complaints DROP COLUMN problem;
    ALTER TABLE driver_complaints RENAME COLUMN problem_new TO problem;

    ALTER TABLE driver_complaints
        ADD CONSTRAINT fk_complaint_problem_master
        FOREIGN KEY (problem) REFERENCES complaint_master(id);

    CREATE INDEX IF NOT EXISTS ix_driver_complaints_problem
        ON driver_complaints (problem);
END $$;

COMMIT;

-- ---------------------------------------------------------------------
-- 4) ตรวจผล — รันแยกหลัง COMMIT
-- ---------------------------------------------------------------------
-- แถวที่มีข้อความเดิมแต่จับคู่ไม่ได้ (ควรว่าง)
SELECT tracking_no, department_id, problem_legacy
FROM   driver_complaints
WHERE  problem IS NULL
  AND  problem_legacy IS NOT NULL
  AND  btrim(problem_legacy) <> ''
ORDER  BY tracking_no;

-- ผลการแปลงทั้งหมด
SELECT d.tracking_no, d.department_id, d.problem AS master_id, m.name, d.problem_legacy
FROM   driver_complaints d
LEFT   JOIN complaint_master m ON m.id = d.problem
WHERE  d.problem_legacy IS NOT NULL
ORDER  BY d.tracking_no;
