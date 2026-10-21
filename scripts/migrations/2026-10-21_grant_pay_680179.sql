-- ให้คุณส้ม (680179 นวรัตน์ แผนกการเงิน) บันทึกการจ่ายเงินได้ (ผู้ใช้สั่ง 21/10/2026 เคส ADV-2610-018)
-- เดิมสิทธิ์ pay มีแค่ 610197 (โบว์) · คุณส้มมีแค่ payee_admin
-- รันใน DBeaver ครั้งเดียว ทำซ้ำได้ · มีผลทันที (หลังบ้านอ่านสิทธิ์สดทุกครั้งที่กดปุ่ม)
BEGIN;

INSERT INTO fin_permissions (employee_id, permission, granted_by) VALUES
  ('680179', 'pay', 'narongkorn.a-2026-10-21')
ON CONFLICT (employee_id, permission) DO NOTHING;

COMMIT;

-- ตรวจผล (อ่านอย่างเดียว) ต้องเห็น pay ของ 610197 และ 680179
SELECT permission, employee_id, granted_by, created_at
FROM fin_permissions
WHERE permission = 'pay' OR employee_id = '680179'
ORDER BY permission, employee_id;
