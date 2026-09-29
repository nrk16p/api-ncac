-- Finance Advance v2 (2026-09-29) — menait-service docs/superpowers/specs/2026-09-29-finance-advance-v2-design.md §6
-- Run once in DBeaver against ncacdb (safe to re-run). Additive: one new table, 2 rules deactivated,
-- 4 questions + options added to form ADV.
BEGIN;

-- 1) approval tiers (clause 6)
CREATE TABLE IF NOT EXISTS fin_approval_tiers (
    clause         varchar(10)   PRIMARY KEY,
    amount_max     numeric(14,2),
    min_level      integer       NOT NULL,
    approver_label varchar(120)  NOT NULL,
    sort_order     integer       NOT NULL,
    CONSTRAINT ck_fin_approval_tiers_min_level CHECK (min_level BETWEEN 1 AND 20)
);

INSERT INTO fin_approval_tiers (clause, amount_max, min_level, approver_label, sort_order) VALUES
    ('6.7',       2000.00, 2, 'Asst. Sup (ระดับ 2)',              1),
    ('6.6',       5000.00, 3, 'Sup / Asst.M (ระดับ 3–4)',         2),
    ('6.5',      20000.00, 5, 'MGR / SM / DPCL (ระดับ 5–7)',      3),
    ('6.4',     100000.00, 8, 'CL (ระดับ 8)',                     4),
    ('6.3',     500000.00, 9, 'DCEO (นโยบายระดับ 9)',             5),
    ('6.2',    1000000.00, 9, 'CEO (นโยบายระดับ 10 → ระบบใช้ 9)', 6),
    ('6.1',          NULL, 9, 'ExC (นโยบายระดับ 11 → ระบบใช้ 9)', 7)
ON CONFLICT (clause) DO NOTHING;

-- 2) the v1 requester-level rules are replaced by the tiers
UPDATE form_approval_rules SET is_active = false
WHERE form_master_id IN (SELECT id FROM form_masters WHERE form_code = 'ADV')
  AND is_active = true;

-- 3) payee questions on ADV
INSERT INTO form_questions (form_master_id, question_name, question_label, question_type,
                            is_required, sort_order, created_at)
SELECT fm.id, q.name, q.label, q.qtype, true, q.sort_order, now()
FROM form_masters fm,
     (VALUES ('adv_cost_center',  'ค่าใช้จ่ายรายศูนย์', 'dropdown', 4),
             ('adv_bank',         'ธนาคาร',           'dropdown', 5),
             ('adv_account_no',   'เลขที่บัญชี',       'text',     6),
             ('adv_account_name', 'ชื่อบัญชี',         'text',     7)) AS q(name, label, qtype, sort_order)
WHERE fm.form_code = 'ADV' AND fm.is_latest = true
  AND NOT EXISTS (SELECT 1 FROM form_questions x WHERE x.form_master_id = fm.id AND x.question_name = q.name);

INSERT INTO form_question_options (question_id, option_value, option_label, sort_order)
SELECT fq.id, o.value, o.label, o.sort_order
FROM form_questions fq
JOIN form_masters fm ON fm.id = fq.form_master_id AND fm.form_code = 'ADV' AND fm.is_latest = true
JOIN (VALUES
        ('adv_cost_center', 'ศลบ',   'ศลบ',                          1),
        ('adv_cost_center', 'สกท',   'สกท',                          2),
        ('adv_cost_center', 'สสบ',   'สสบ',                          3),
        ('adv_cost_center', 'ศรย',   'ศรย',                          4),
        ('adv_cost_center', 'ศขก',   'ศขก',                          5),
        ('adv_cost_center', 'ศบก',   'ศบก',                          6),
        ('adv_bank',        'BBL',   'ธนาคารกรุงเทพ',                1),
        ('adv_bank',        'KBANK', 'ธนาคารกสิกรไทย',               2),
        ('adv_bank',        'KTB',   'ธนาคารกรุงไทย',                3),
        ('adv_bank',        'SCB',   'ธนาคารไทยพาณิชย์',             4),
        ('adv_bank',        'BAY',   'ธนาคารกรุงศรีอยุธยา',          5),
        ('adv_bank',        'TTB',   'ธนาคารทหารไทยธนชาต',           6),
        ('adv_bank',        'GSB',   'ธนาคารออมสิน',                 7),
        ('adv_bank',        'BAAC',  'ธ.ก.ส.',                       8),
        ('adv_bank',        'GHB',   'ธนาคารอาคารสงเคราะห์',         9),
        ('adv_bank',        'UOB',   'ธนาคารยูโอบี',                 10),
        ('adv_bank',        'CIMBT', 'ธนาคารซีไอเอ็มบี ไทย',         11),
        ('adv_bank',        'LHB',   'ธนาคารแลนด์ แอนด์ เฮ้าส์',     12),
        ('adv_bank',        'KKP',   'ธนาคารเกียรตินาคินภัทร',       13),
        ('adv_bank',        'TISCO', 'ธนาคารทิสโก้',                 14),
        ('adv_bank',        'ICBCT', 'ธนาคารไอซีบีซี (ไทย)',         15),
        ('adv_bank',        'IBANK', 'ธนาคารอิสลามแห่งประเทศไทย',    16)
     ) AS o(qname, value, label, sort_order) ON o.qname = fq.question_name
WHERE NOT EXISTS (SELECT 1 FROM form_question_options x WHERE x.question_id = fq.id AND x.option_value = o.value);

COMMIT;
