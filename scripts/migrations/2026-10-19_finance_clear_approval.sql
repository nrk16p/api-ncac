-- ด่านอนุมัติการเคลียร์เงิน (ผู้ใช้สั่ง 19/10/2026)
-- "ตอนเคลียร์ ก่อนถึงบัญชี ต้องผ่านการอนุมัติตามขั้น เหมือนตอนขอเบิก"
-- ชั้นอนุมัติตัดด้วยยอดที่ใช้จริง · ส่งกลับแก้แล้วขึ้นอนุมัติใหม่เฉพาะตอนข้ามชั้น
-- รันใน DBeaver ครั้งเดียว ทำซ้ำได้ · รันก่อนหรือหลัง deploy ก็ได้ ใบเก่าไม่กระทบ
BEGIN;

ALTER TABLE fin_advances
    ADD COLUMN IF NOT EXISTS clear_approval_level  integer,
    ADD COLUMN IF NOT EXISTS clear_approved_clause varchar(10);

COMMENT ON COLUMN fin_advances.clear_approval_level  IS 'ขั้นอนุมัติเคลียร์ที่ค้างอยู่ 1=หัวหน้า 2=ผู้มีอำนาจ · NULL=ไม่ได้รออนุมัติ';
COMMENT ON COLUMN fin_advances.clear_approved_clause IS 'ข้อ TOA ที่อนุมัติเคลียร์ไปแล้ว ใช้ดูว่าแก้ใหม่แล้วข้ามชั้นไหม';

CREATE TABLE IF NOT EXISTS fin_clear_approval_logs (
    id            serial       PRIMARY KEY,
    form_id       varchar(80)  NOT NULL,
    step          integer      NOT NULL,
    action        varchar(20)  NOT NULL,     -- SUBMITTED / APPROVED / REJECTED
    clause        varchar(10),
    amount_actual numeric(12,2),
    remark        text,
    action_by     varchar(50),
    created_at    timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_fin_clear_approval_logs_form_id ON fin_clear_approval_logs (form_id);

-- สถานะใหม่ CLEAR_PENDING ต้องอยู่ใน CHECK ของ fin_status
ALTER TABLE fin_advances DROP CONSTRAINT IF EXISTS ck_fin_advances_fin_status;
ALTER TABLE fin_advances ADD CONSTRAINT ck_fin_advances_fin_status CHECK (
    fin_status IS NULL OR fin_status IN (
        'VOUCHERED','VOUCHER_REJECTED','RETURNED','RESUBMITTED','PAID',
        'CLEAR_PENDING','CLEARING_SUBMITTED','SENT_BACK','CLOSED'
    )
);

COMMIT;

-- ตรวจผล (อ่านอย่างเดียว)
SELECT column_name FROM information_schema.columns
WHERE table_name = 'fin_advances' AND column_name LIKE 'clear_%' ORDER BY column_name;
SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
WHERE conname = 'ck_fin_advances_fin_status';
SELECT count(*) AS ใบที่รออนุมัติเคลียร์ FROM fin_advances WHERE fin_status = 'CLEAR_PENDING';
