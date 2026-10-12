-- ผู้ใช้สั่ง 2026-10-13: ไม่มี Project Code แยก — เลขที่ใบใช้วิธีรันเลขแบบนั้นแทน (LB-B02-2610-001)
-- เฉพาะฐานที่เคยรัน 2026-10-13_petty_project_code.sql · รันซ้ำได้
ALTER TABLE fin_petty_claims DROP COLUMN IF EXISTS project_code;
