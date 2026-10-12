-- Project Code บนใบสำคัญจ่ายเงินสดย่อย: ระบบรันเลขเอง ชุดแยกจากเลขที่ใบเบิก
-- รูปแบบ PJ-{LB|SB}-{ดิวิชัน}-{ปีเดือน}-{ลำดับ} เช่น PJ-LB-B02-2610-001
-- ไฟล์นี้จำเป็นเฉพาะ DB ที่รัน 2026-10-12_finance_petty_cash.sql ไปก่อนแล้ว
-- (ฉบับนั้นมีคอลัมน์นี้อยู่ในคำสั่งสร้างตารางแล้ว) · รันซ้ำได้ ไม่พัง

ALTER TABLE fin_petty_claims ADD COLUMN IF NOT EXISTS project_code varchar(40);
