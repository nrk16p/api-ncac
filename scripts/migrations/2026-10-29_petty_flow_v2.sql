-- เงินสดย่อย flow v2 (ผู้ใช้สั่ง 2026-10-29) — ดู petty-cash-requirement/SPEC-FLOW-V2.md
-- ขออนุมัติ → เจ้าของเงินสดอนุมัติพร้อมจ่าย → ส่งเคลียร์เป็นรอบ → หัวหน้าตามสายงานอนุมัติเคลียร์
-- → บัญชีตรวจทีละใบ → ตั้งเบิกทั้งรอบ → การเงินทำจ่าย → ปิด
--
-- ต้องรัน **ก่อน** deploy BE ที่มี flow v2 (ไม่งั้น SELECT fin_petty_batches พังเพราะไม่มีคอลัมน์ status)
-- รันซ้ำได้ · create_all ไม่เพิ่มคอลัมน์ให้ตารางที่มีอยู่แล้ว จึงต้องรันไฟล์นี้เอง

BEGIN;

-- 1) สถานะใบ: เพิ่ม CLEAR_PENDING / REVIEWED · TRANSFERRED ยังอยู่ใน CHECK ให้แถวเก่าไม่ผิดกติกา
ALTER TABLE fin_petty_claims DROP CONSTRAINT IF EXISTS ck_fin_petty_claims_status;
ALTER TABLE fin_petty_claims ADD CONSTRAINT ck_fin_petty_claims_status CHECK (status IN
  ('PENDING_APPROVAL','RETURNED','APPROVED','CLEAR_PENDING','AWAITING_REVIEW','SENT_BACK','REVIEWED','CLOSED',
   'TRANSFERRED'));

-- ใบที่โอนแล้วตาม flow เดิม = อนุมัติจ่ายแล้ว รอส่งเคลียร์ ใน flow ใหม่
UPDATE fin_petty_claims SET status = 'APPROVED', updated_at = now() WHERE status = 'TRANSFERRED';

-- 2) รอบส่งเคลียร์
-- รอบเก่าทุกรอบคือรอบที่การเงินปิดไปแล้ว → เติม CLOSED ตอนเพิ่มคอลัมน์ แล้วค่อยเปลี่ยนค่าเริ่มต้นเป็นของ flow ใหม่
ALTER TABLE fin_petty_batches ADD COLUMN IF NOT EXISTS status varchar(30) NOT NULL DEFAULT 'CLOSED';
ALTER TABLE fin_petty_batches ALTER COLUMN status SET DEFAULT 'CLEAR_PENDING';

ALTER TABLE fin_petty_batches ADD COLUMN IF NOT EXISTS submitted_by         varchar(50);
ALTER TABLE fin_petty_batches ADD COLUMN IF NOT EXISTS submitted_at         timestamptz;
ALTER TABLE fin_petty_batches ADD COLUMN IF NOT EXISTS clear_required_level int;
ALTER TABLE fin_petty_batches ADD COLUMN IF NOT EXISTS clear_approved_by    varchar(50);
ALTER TABLE fin_petty_batches ADD COLUMN IF NOT EXISTS clear_approved_at    timestamptz;
ALTER TABLE fin_petty_batches ADD COLUMN IF NOT EXISTS clear_reject_reason  text;
ALTER TABLE fin_petty_batches ADD COLUMN IF NOT EXISTS voucher_no           varchar(50);
ALTER TABLE fin_petty_batches ADD COLUMN IF NOT EXISTS vouchered_by         varchar(50);
ALTER TABLE fin_petty_batches ADD COLUMN IF NOT EXISTS vouchered_at         timestamptz;
ALTER TABLE fin_petty_batches ADD COLUMN IF NOT EXISTS pay_date             date;
ALTER TABLE fin_petty_batches ADD COLUMN IF NOT EXISTS paid_by              varchar(50);
ALTER TABLE fin_petty_batches ADD COLUMN IF NOT EXISTS paid_at              timestamptz;

-- closed_at ว่างจนกว่าการเงินจะทำจ่าย (เดิมเป็น NOT NULL DEFAULT now() เพราะรอบเกิดตอนปิดเท่านั้น)
ALTER TABLE fin_petty_batches ALTER COLUMN closed_at DROP NOT NULL;
ALTER TABLE fin_petty_batches ALTER COLUMN closed_at DROP DEFAULT;

ALTER TABLE fin_petty_batches DROP CONSTRAINT IF EXISTS ck_fin_petty_batches_status;
ALTER TABLE fin_petty_batches ADD CONSTRAINT ck_fin_petty_batches_status CHECK (status IN
  ('CLEAR_PENDING','CLEAR_REJECTED','AWAITING_REVIEW','REVIEWED','VOUCHERED','CLOSED'));

COMMIT;

-- ตรวจหลังรัน (อ่านอย่างเดียว)
-- SELECT status, count(*) FROM fin_petty_claims GROUP BY status ORDER BY status;
-- SELECT status, count(*) FROM fin_petty_batches GROUP BY status ORDER BY status;
-- ใบ AWAITING_REVIEW / SENT_BACK ที่ไม่มีรอบ (ค้างจาก flow เดิม เดินต่อใน flow ใหม่ไม่ได้ — ล้างพร้อมใบทดสอบ):
-- SELECT form_id, status FROM fin_petty_claims WHERE status IN ('AWAITING_REVIEW','SENT_BACK') AND batch_id IS NULL;
