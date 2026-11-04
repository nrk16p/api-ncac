-- ตีกลับแบบยกเลิก (ผู้ใช้สั่ง 04/11/2026): สถานะใหม่ CANCELLED ของ fin_advances.fin_status
-- รันบน prod ncacdb ก่อน deploy api-ncac branch feat/advance-cancel (ไม่งั้นปุ่มยกเลิกตอบ 500 "ฐานข้อมูลยังไม่รองรับสถานะนี้")
-- รันซ้ำได้ (DROP IF EXISTS + ADD) · ไม่แตะข้อมูลเดิม

BEGIN;

ALTER TABLE fin_advances DROP CONSTRAINT IF EXISTS ck_fin_advances_fin_status;
ALTER TABLE fin_advances ADD CONSTRAINT ck_fin_advances_fin_status CHECK (
    fin_status IS NULL OR fin_status IN (
        'VOUCHERED','VOUCHER_REJECTED','RETURNED','RESUBMITTED','PAID',
        'CLEAR_PENDING','CLEARING_SUBMITTED','SENT_BACK','CLOSED','CANCELLED'
    )
);

COMMIT;

-- ตรวจผล (อ่านอย่างเดียว) ต้องเห็น 'CANCELLED' ในนิยาม CHECK
SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_fin_advances_fin_status';
