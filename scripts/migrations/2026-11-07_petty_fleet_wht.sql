-- เงินสดย่อย 7/11/2026: ฟลีท ณ วันทำรายการ + รหัสคนขับต่อแถว (ข้อ 7/10) · รหัสใบสำคัญจ่าย (ข้อ 8) · ยอดหัก ณ ที่จ่ายต่อใบ (ข้อ 12)
-- รัน **ก่อน** deploy BE ชุดนี้ (ไม่งั้น SELECT ใบพังเพราะไม่มีคอลัมน์) · รันซ้ำได้ · ตาราง TOA (fin_approval_tiers) ใช้ของ Advance ที่มีอยู่แล้ว

BEGIN;

ALTER TABLE fin_petty_claim_items ADD COLUMN IF NOT EXISTS fleet varchar(30);
ALTER TABLE fin_petty_claim_items ADD COLUMN IF NOT EXISTS driver_code varchar(30);

ALTER TABLE fin_petty_claims ADD COLUMN IF NOT EXISTS wht_amount numeric(12,2) NOT NULL DEFAULT 0;
-- รหัสใบสำคัญจ่าย 2100/2500/3200/3300/3500 จากประเภทคนขับ+ฟลีท (ข้อ 8) — คนละช่องกับ cost_center (แผนกผู้เบิก)
ALTER TABLE fin_petty_claims ADD COLUMN IF NOT EXISTS expense_code varchar(8);

COMMIT;

-- ตรวจหลังรัน (อ่านอย่างเดียว):
-- SELECT column_name FROM information_schema.columns WHERE table_name='fin_petty_claim_items' AND column_name IN ('fleet','driver_code');
-- SELECT column_name FROM information_schema.columns WHERE table_name='fin_petty_claims' AND column_name='wht_amount';
