-- =====================================================================
-- Finance — เบิกเงิน Advance  (28 ก.ย. 2026)
--
-- 0) ขยาย CHECK form_masters.form_type ให้รับ 'Advance' (บน prod เดิมรับแค่ Issue/Service)
-- 1) ตาราง fin_accounts / fin_advances / fin_advance_logs (ตรงกับ models/finance_model.py)
-- 2) seed รหัสบัญชีเงินสดย่อย 11 รายการ
-- 3) seed ฟอร์ม ADV (form_type='Advance') + คำถาม 3 ข้อ + กฎอนุมัติ 2 ข้อ (ขั้นเดียว)
--
-- รันซ้ำได้ (idempotent) · ไม่มี DO $$ block (รันใน DBeaver แบบ script ได้: Alt+X)
-- ฟอร์ม ADV ไม่ขึ้นหน้า home ของเว็บจริง (home แสดงเฉพาะ form_type='Service')
-- =====================================================================

BEGIN;

-- 0) allow form_type 'Advance'
ALTER TABLE form_masters DROP CONSTRAINT IF EXISTS form_masters_form_type_check;
ALTER TABLE form_masters ADD CONSTRAINT form_masters_form_type_check
    CHECK (form_type IN ('Issue', 'Service', 'Advance'));

-- 1) tables
CREATE TABLE IF NOT EXISTS fin_accounts (
    acc_code     varchar(20)  PRIMARY KEY,
    acc_name     varchar(255) NOT NULL,
    acc_name_en  varchar(255),
    is_active    boolean      NOT NULL DEFAULT true,
    created_at   timestamptz  NOT NULL DEFAULT now(),
    updated_at   timestamptz  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fin_advances (
    id                  serial        PRIMARY KEY,
    submission_id       integer       NOT NULL UNIQUE REFERENCES form_submissions(id),
    form_id             varchar(80)   NOT NULL UNIQUE,
    acc_code            varchar(20)   REFERENCES fin_accounts(acc_code),
    voucher_no          varchar(50),
    voucher_date        date,
    payment_doc_no      varchar(50),
    purpose             text,
    amount_paid         numeric(12,2) NOT NULL,
    transfer_date       date          NOT NULL,
    clear_due_date      date          NOT NULL,
    paid_by             varchar(50),
    paid_at             timestamptz,
    clear_date          date,
    amount_actual       numeric(12,2),
    clear_doc_no        varchar(100),
    settle_amount       numeric(12,2),
    settle_date         date,
    remark              text,
    clear_submitted_at  timestamptz,
    review_remark       text,
    closed_by           varchar(50),
    closed_at           timestamptz,
    fin_status          varchar(30)   NOT NULL,
    created_at          timestamptz   NOT NULL DEFAULT now(),
    updated_at          timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT ck_fin_advances_amount_paid   CHECK (amount_paid >= 0),
    CONSTRAINT ck_fin_advances_amount_actual CHECK (amount_actual IS NULL OR amount_actual >= 0),
    CONSTRAINT ck_fin_advances_fin_status    CHECK (fin_status IN ('PAID','CLEARING_SUBMITTED','SENT_BACK','CLOSED'))
);
CREATE INDEX IF NOT EXISTS ix_fin_advances_fin_status     ON fin_advances (fin_status);
CREATE INDEX IF NOT EXISTS ix_fin_advances_clear_due_date ON fin_advances (clear_due_date);

CREATE TABLE IF NOT EXISTS fin_advance_logs (
    id          serial       PRIMARY KEY,
    advance_id  integer      NOT NULL REFERENCES fin_advances(id) ON DELETE CASCADE,
    action      varchar(30)  NOT NULL,
    changes     jsonb,
    remark      text,
    action_by   varchar(50),
    created_at  timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_fin_advance_logs_advance_id ON fin_advance_logs (advance_id);

-- 2) petty-cash accounts
INSERT INTO fin_accounts (acc_code, acc_name, acc_name_en) VALUES
    ('110101', 'เงินสดย่อย-กรุงเทพฯ - บัญชี คุณอัจฉราพร',       'Petty Cash - Bangkok. - Accounting'),
    ('110102', 'เงินสดย่อย-สระบุรี - บัญชี คุณศิวพร',           'Petty Cash - Saraburi - Accounting'),
    ('110103', 'เงินสดย่อย-ลาดกระบัง - บัญชี คุณวรรษชล',        'Petty Cash - Ladkrabang - Accounting'),
    ('110104', 'เงินสดย่อย-ลาดกระบัง - ยย. คุณนวลจันทร์',       'Petty Cash - Ladkrabang - Automotive'),
    ('110105', 'เงินสดย่อย-สระบุรี - ยย คุณสุทธิพงษ์',           'Petty Cash - Saraburi - Automotive'),
    ('110106', 'เงินสดย่อย-ขอนแก่น - ยย. คุณณัฐณิชา',          'Petty Cash - Khonkaen - Automotive'),
    ('110107', 'เงินสดย่อย-ค่าปรับ คุณธันย์ณภัทร',               'Petty Cash - Fines'),
    ('110108', 'เงินสดย่อย-ลาดกระบัง - พจส. คุณจุฑารัตน์',      'Petty Cash - Ladkrabang - Driver'),
    ('110109', 'เงินสดย่อย-สระบุรี - พจส. คุณกัลยณัฐ',          'Petty Cash - Saraburi - Driver'),
    ('110110', 'เงินสดย่อย-ลาดกระบัง - จป. คุณวรรณา',          'Petty Cash - Ladkrabang - Safety'),
    ('110111', 'เงินสดย่อย-ลาดกระบัง - บุคคล คุณณชญาดา',       'Petty Cash - Ladkrabang - HR')
ON CONFLICT (acc_code) DO NOTHING;

-- 3) ADV form + 3 questions + 2 approval rules — inserted only when ADV does not exist yet
--    ขั้นเดียว (level_no = 1 ทั้งคู่): ผู้ขอ 1–4 → ผู้อนุมัติ 5–6 · ผู้ขอ 5–6 → ผู้อนุมัติ 7–8
--    ผู้ขอระดับ 7–9 ไม่เข้ากฎใด → อนุมัติอัตโนมัติ (พฤติกรรมเดิมของ engine)
WITH new_form AS (
    INSERT INTO form_masters (form_type, form_code, form_name, form_status, need_approval,
                              created_at, version, parent_form_id, is_latest)
    SELECT 'Advance', 'ADV', 'เบิกเงิน Advance', 'Active', true, now(), 1, NULL, true
    WHERE NOT EXISTS (SELECT 1 FROM form_masters WHERE form_code = 'ADV')
    RETURNING id
), new_questions AS (
    INSERT INTO form_questions (form_master_id, question_name, question_label, question_type,
                                is_required, sort_order, created_at)
    SELECT new_form.id, q.name, q.label, q.qtype, true, q.sort_order, now()
    FROM new_form,
         (VALUES ('adv_purpose',  'เบิกเงิน Advance สำหรับ', 'longtext', 1),
                 ('adv_amount',   'จำนวนเงิน',              'number',   2),
                 ('adv_use_date', 'วันที่ใช้เงิน',            'datetime', 3)) AS q(name, label, qtype, sort_order)
    RETURNING id
)
INSERT INTO form_approval_rules (form_master_id, creator_min, creator_max, level_no, approve_by_type,
                                 approve_by_min, approve_by_max, same_department, is_active, created_at)
SELECT new_form.id, r.creator_min, r.creator_max, 1, 'position_level_range', r.approve_min, r.approve_max, true, true, now()
FROM new_form,
     (VALUES (1, 4, 5, 6),
             (5, 6, 7, 8)) AS r(creator_min, creator_max, approve_min, approve_max);

COMMIT;
