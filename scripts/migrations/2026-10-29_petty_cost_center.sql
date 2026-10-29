-- เงินสดย่อย: ศูนย์และแผนกของผู้เบิก (ผู้ใช้สั่ง 2026-10-29)
--
-- หน้าเปิดใบเลือกจาก dropdown 43 รหัส (1400 … 3B07) เก็บที่ fin_petty_claims.cost_center
-- ใบสำคัญจ่ายพิมพ์รหัสนี้ในช่อง "รหัสค่าใช้จ่ายของผู้เบิก (บัญชี)" · ใบเก่าว่าง (พิมพ์รหัสแผนกของวงเงินแทน)
--
-- ต้องรัน **ก่อน** deploy BE ที่อ่านคอลัมน์ใหม่ · รันซ้ำได้
-- รันหลัง 2026-10-29_petty_driver_role_tire.sql

BEGIN;

ALTER TABLE fin_petty_claims ADD COLUMN IF NOT EXISTS cost_center varchar(8);

COMMIT;
