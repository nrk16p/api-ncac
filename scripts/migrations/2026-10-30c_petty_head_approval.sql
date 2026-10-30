-- เงินสดย่อย flow v3 (ผู้ใช้สั่ง 2026-10-30): หัวหน้าของผู้ขอเบิกอนุมัติก่อน เจ้าของเงินสดถึงโอนได้
-- ผู้ขอเบิกส่ง → PENDING_HEAD (รอหัวหน้าอนุมัติ) → PENDING_APPROVAL (รอเจ้าของเงินสดอนุมัติ) → APPROVED ...
--
-- ต้องรัน **ก่อน** deploy BE ที่มี flow v3 (ไม่งั้นเปิดใบใหม่ไม่ได้ เพราะ CHECK ไม่รู้จัก PENDING_HEAD
-- และ SELECT ใบพังเพราะไม่มีคอลัมน์ head_approved_by) · รันซ้ำได้

BEGIN;

ALTER TABLE fin_petty_claims ADD COLUMN IF NOT EXISTS head_approved_by varchar(50);
ALTER TABLE fin_petty_claims ADD COLUMN IF NOT EXISTS head_approved_at timestamptz;

ALTER TABLE fin_petty_claims DROP CONSTRAINT IF EXISTS ck_fin_petty_claims_status;
ALTER TABLE fin_petty_claims ADD CONSTRAINT ck_fin_petty_claims_status CHECK (status IN
  ('PENDING_HEAD','PENDING_APPROVAL','RETURNED','APPROVED','CLEAR_PENDING','AWAITING_REVIEW','SENT_BACK',
   'REVIEWED','CLOSED','TRANSFERRED'));

COMMIT;

-- ใบที่ค้าง "รอเจ้าของเงินสดอนุมัติ" อยู่ก่อนรันไฟล์นี้ ยังเดินต่อได้ตามเดิม (ไม่ย้อนไปให้หัวหน้า)
-- ตรวจหลังรัน (อ่านอย่างเดียว):
-- SELECT status, count(*) FROM fin_petty_claims GROUP BY status ORDER BY status;
