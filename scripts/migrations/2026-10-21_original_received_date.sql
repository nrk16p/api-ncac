-- ปิดรายการต้องติ๊กรับเอกสารตัวจริง + วันที่รับ (ผู้ใช้สั่ง 21/10/2026)
-- ต้องรันก่อน deploy หลังบ้าน commit นี้ ไม่งั้นหน้าเบิกเงินจะ error เพราะหาคอลัมน์ไม่เจอ
-- รันใน DBeaver ครั้งเดียว ทำซ้ำได้ · เพิ่มคอลัมน์ว่าง ไม่แตะข้อมูลเดิม
ALTER TABLE fin_advances ADD COLUMN IF NOT EXISTS original_received_date date;

-- ตรวจผล (อ่านอย่างเดียว) ต้องเห็น 1 แถว
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name = 'fin_advances' AND column_name = 'original_received_date';
