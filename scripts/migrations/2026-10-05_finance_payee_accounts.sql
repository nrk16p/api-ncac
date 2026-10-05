-- Finance payee account master (K-Bank) + ADV payee type question. Run in DBeaver (one transaction, idempotent).
-- Run BEFORE starting the BE that contains this change.
BEGIN;

-- 1) requests (created first: the master references it)
CREATE TABLE IF NOT EXISTS fin_payee_account_requests (
    id            serial       PRIMARY KEY,
    employee_id   varchar(50)  NOT NULL,
    bank          varchar(20)  NOT NULL DEFAULT 'KBANK',
    account_no    varchar(20)  NOT NULL,
    account_name  varchar(150) NOT NULL,
    remark        text,
    status        varchar(10)  NOT NULL DEFAULT 'PENDING',
    review_remark text,
    reviewed_by   varchar(50),
    reviewed_at   timestamptz,
    created_at    timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT ck_fin_payee_requests_bank   CHECK (bank = 'KBANK'),
    CONSTRAINT ck_fin_payee_requests_status CHECK (status IN ('PENDING','APPROVED','REJECTED','CANCELLED'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_fin_payee_requests_one_pending
    ON fin_payee_account_requests (employee_id) WHERE status = 'PENDING';

-- 2) master
CREATE TABLE IF NOT EXISTS fin_payee_accounts (
    id                serial       PRIMARY KEY,
    employee_id       varchar(50)  NOT NULL UNIQUE,
    bank              varchar(20)  NOT NULL DEFAULT 'KBANK',
    account_no        varchar(20)  NOT NULL,
    account_name      varchar(150) NOT NULL,
    status            varchar(10)  NOT NULL DEFAULT 'ACTIVE',
    source_request_id integer      REFERENCES fin_payee_account_requests(id) ON DELETE SET NULL,
    created_by        varchar(50),
    created_at        timestamptz  NOT NULL DEFAULT now(),
    updated_by        varchar(50),
    updated_at        timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT ck_fin_payee_accounts_bank   CHECK (bank = 'KBANK'),
    CONSTRAINT ck_fin_payee_accounts_status CHECK (status IN ('ACTIVE','INACTIVE'))
);

-- 3) audit log
CREATE TABLE IF NOT EXISTS fin_payee_account_logs (
    id          serial      PRIMARY KEY,
    employee_id varchar(50) NOT NULL,
    action      varchar(30) NOT NULL,
    changes     jsonb,
    remark      text,
    action_by   varchar(50),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_fin_payee_account_logs_employee_id ON fin_payee_account_logs (employee_id);

-- 4) ADV question adv_payee_type (sort 5); bank/account_no/account_name move to 6/7/8
INSERT INTO form_questions (form_master_id, question_name, question_label, question_type,
                            is_required, sort_order, created_at)
SELECT fm.id, 'adv_payee_type', 'บัญชีรับเงิน', 'dropdown', true, 5, now()
FROM form_masters fm
WHERE fm.form_code = 'ADV' AND fm.is_latest = true
  AND NOT EXISTS (SELECT 1 FROM form_questions x WHERE x.form_master_id = fm.id AND x.question_name = 'adv_payee_type');

INSERT INTO form_question_options (question_id, option_value, option_label, sort_order)
SELECT fq.id, o.value, o.label, o.sort_order
FROM form_questions fq
JOIN form_masters fm ON fm.id = fq.form_master_id AND fm.form_code = 'ADV' AND fm.is_latest = true
JOIN (VALUES
        ('adv_payee_type', 'SELF',     'บัญชีตัวเอง',     1),
        ('adv_payee_type', 'SUPPLIER', 'บัญชี Supplier', 2)
     ) AS o(qname, value, label, sort_order) ON o.qname = fq.question_name
WHERE NOT EXISTS (SELECT 1 FROM form_question_options x WHERE x.question_id = fq.id AND x.option_value = o.value);

UPDATE form_questions fq SET sort_order = v.sort_order
FROM form_masters fm, (VALUES ('adv_payee_type', 5), ('adv_bank', 6), ('adv_account_no', 7), ('adv_account_name', 8)) AS v(name, sort_order)
WHERE fm.id = fq.form_master_id AND fm.form_code = 'ADV' AND fm.is_latest = true
  AND fq.question_name = v.name AND fq.sort_order IS DISTINCT FROM v.sort_order;

COMMIT;

-- verify (read-only)
SELECT table_name, count(*) AS columns FROM information_schema.columns
WHERE table_name IN ('fin_payee_accounts','fin_payee_account_requests','fin_payee_account_logs') GROUP BY table_name ORDER BY table_name;
SELECT indexname FROM pg_indexes WHERE tablename LIKE 'fin_payee_%' ORDER BY indexname;
SELECT fq.question_name, fq.sort_order, fq.is_required FROM form_questions fq
JOIN form_masters fm ON fm.id = fq.form_master_id AND fm.form_code = 'ADV' AND fm.is_latest = true
WHERE fq.question_name LIKE 'adv\_%' ORDER BY fq.sort_order;
SELECT fq.question_name, o.option_value, o.option_label, o.sort_order FROM form_question_options o
JOIN form_questions fq ON fq.id = o.question_id AND fq.question_name = 'adv_payee_type'
JOIN form_masters fm ON fm.id = fq.form_master_id AND fm.form_code = 'ADV' AND fm.is_latest = true ORDER BY o.sort_order;
