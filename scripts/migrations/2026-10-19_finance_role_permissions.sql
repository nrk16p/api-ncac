-- สิทธิ์ตามหน้าที่ของงานเบิกเงิน Advance (ผู้ใช้ตัดสิน 19/10/2026)
-- เดิม: ใครอยู่แผนกการเงิน (4) หรือบัญชี (6) กดได้ทุกปุ่ม
-- ใหม่: แยกตามหน้าที่จริง — สิทธิ์ใดที่ยังไม่มีแถวเลย ระบบจะใช้กติกาเดิมของสิทธิ์นั้นต่อไป
-- รันใน DBeaver ครั้งเดียว ทำซ้ำได้
BEGIN;

CREATE TABLE IF NOT EXISTS fin_permissions (
    employee_id varchar(50)  NOT NULL,
    permission  varchar(30)  NOT NULL,
    granted_by  varchar(50),
    created_at  timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (employee_id, permission)
);

CREATE INDEX IF NOT EXISTS ix_fin_permissions_permission ON fin_permissions (permission);

INSERT INTO fin_permissions (employee_id, permission, granted_by) VALUES
  ('690041', 'voucher',         'SETUP-2026-10-19'),  -- สุขพัฒน์ (พีช) ตั้งเบิก
  ('610197', 'pay',             'SETUP-2026-10-19'),  -- แพรวนภา (โบว์) จ่ายเงิน
  ('690041', 'return',          'SETUP-2026-10-19'),  -- ตีกลับให้ผู้เบิกแก้ไข
  ('610197', 'return',          'SETUP-2026-10-19'),
  ('660548', 'clearing_review', 'SETUP-2026-10-19'),  -- ชญาดา (น้อง) ตรวจเคลียร์
  ('670163', 'clearing_review', 'SETUP-2026-10-19'),  -- ณิชกานต์ (เบล) ตรวจเคลียร์
  ('620420', 'close',           'SETUP-2026-10-19'),  -- ดารณี (จอย) รับเงินคืน + ปิดรายการ
  ('680179', 'payee_admin',     'SETUP-2026-10-19'),  -- นวรัตน์ (ส้ม) ตรวจเลขที่บัญชี
  ('660548', 'overdue_remind',  'SETUP-2026-10-19'),
  ('670163', 'overdue_remind',  'SETUP-2026-10-19'),
  ('620420', 'overdue_remind',  'SETUP-2026-10-19')
ON CONFLICT (employee_id, permission) DO NOTHING;

COMMIT;

-- ตรวจผล (อ่านอย่างเดียว)
SELECT p.permission, p.employee_id,
       trim(coalesce(u.firstname,'') || ' ' || coalesce(u.lastname,'')) AS ชื่อ
FROM fin_permissions p LEFT JOIN users u ON u.employee_id = p.employee_id
ORDER BY p.permission, p.employee_id;
