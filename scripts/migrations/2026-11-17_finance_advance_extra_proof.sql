-- สถานะใหม่ "ปิด (รอแนบหลักฐานจากการเงิน)" (ผู้ใช้สั่ง 17/11/2026): fin_advances.fin_status = CLOSED_PENDING_PROOF
-- ใช้เฉพาะใบที่เบิกเพิ่ม (settle_amount < 0) — บัญชีปิดรายการแล้ว รอการเงินโอนเงินเพิ่มและแนบหลักฐาน
-- รันบน prod ncacdb ได้ทั้งก่อนหรือหลัง deploy api-ncac: ก่อนรัน BE ยังให้บัญชีใส่วันโอนเพิ่มก่อนปิดเหมือนเดิม
-- รันซ้ำได้ (DROP IF EXISTS + ADD) · ไม่แตะข้อมูลเดิม · รายการค่าเดิมเอามาจาก 2026-11-04_finance_advance_cancel.sql

BEGIN;

ALTER TABLE fin_advances DROP CONSTRAINT IF EXISTS ck_fin_advances_fin_status;
ALTER TABLE fin_advances ADD CONSTRAINT ck_fin_advances_fin_status CHECK (
    fin_status IS NULL OR fin_status IN (
        'VOUCHERED','VOUCHER_REJECTED','RETURNED','RESUBMITTED','PAID',
        'CLEAR_PENDING','CLEARING_SUBMITTED','SENT_BACK','CLOSED','CANCELLED',
        'CLOSED_PENDING_PROOF'
    )
);

COMMIT;

-- ตรวจผล (อ่านอย่างเดียว) ต้องเห็น 'CLOSED_PENDING_PROOF' และ 'CANCELLED' ในนิยาม CHECK
SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_fin_advances_fin_status';
