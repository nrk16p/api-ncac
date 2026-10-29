-- Access Review (ทบทวนสิทธิ์ผู้ใช้) — new tables only, existing tables untouched (2026-10-29)
--
-- Mirrors models/access_review_model.py. Additive and idempotent (IF NOT EXISTS / ON CONFLICT),
-- safe to run before or after deploying the code. main.py's create_all() would also create the
-- tables on boot, but NOT the ar_systems seed below — run this file so the 3 systems exist
-- (the importer also upserts its own system row on --commit).
--
-- employee_id columns hold users.employee_id (string, no FK) like form_approver_divisions.
-- Roles / user_roles are soft-deleted (is_active=false) so review history stays intact.
-- 9 tables: ar_systems, ar_roles, ar_role_items, ar_user_roles, ar_cycles, ar_reviews, ar_review_lines,
-- ar_review_line_items, ar_review_approvers. Includes 2026-10-30_access_review_item_review.sql. (A DB built from the earlier draft with ar_accounts/ar_account_roles: run 2026-10-29b.)
BEGIN;

CREATE TABLE IF NOT EXISTS ar_systems (
    id        SERIAL PRIMARY KEY,
    code      VARCHAR(30)  NOT NULL,
    name      VARCHAR(255) NOT NULL,
    is_active BOOLEAN      NOT NULL DEFAULT TRUE,
    CONSTRAINT ar_systems_code_key UNIQUE (code)
);

CREATE TABLE IF NOT EXISTS ar_roles (
    id        SERIAL PRIMARY KEY,
    system_id INTEGER      NOT NULL REFERENCES ar_systems (id),
    name      VARCHAR(500) NOT NULL,
    is_active BOOLEAN      NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_ar_roles_system_name UNIQUE (system_id, name)
);

CREATE TABLE IF NOT EXISTS ar_role_items (
    id         SERIAL PRIMARY KEY,
    role_id    INTEGER      NOT NULL REFERENCES ar_roles (id),
    category   VARCHAR(500) NOT NULL,
    item       VARCHAR(500),
    permission VARCHAR(255),
    is_active  BOOLEAN      NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS ix_ar_role_items_role_id ON ar_role_items (role_id);

CREATE TABLE IF NOT EXISTS ar_user_roles (
    id           SERIAL PRIMARY KEY,
    employee_id  VARCHAR(50) NOT NULL,
    role_id      INTEGER     NOT NULL REFERENCES ar_roles (id),
    account_name VARCHAR(255),
    is_active    BOOLEAN     NOT NULL DEFAULT TRUE,
    updated_at   TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_ar_user_roles_employee_role UNIQUE (employee_id, role_id)
);
CREATE INDEX IF NOT EXISTS ix_ar_user_roles_employee_id ON ar_user_roles (employee_id);
CREATE INDEX IF NOT EXISTS ix_ar_user_roles_role_id ON ar_user_roles (role_id);

CREATE TABLE IF NOT EXISTS ar_cycles (
    id         SERIAL PRIMARY KEY,
    name       VARCHAR(100) NOT NULL,
    start_date DATE         NOT NULL,
    end_date   DATE         NOT NULL,
    is_open    BOOLEAN      NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS ar_reviews (
    id           SERIAL PRIMARY KEY,
    cycle_id     INTEGER     NOT NULL REFERENCES ar_cycles (id),
    employee_id  VARCHAR(50) NOT NULL,
    status       VARCHAR(20) NOT NULL DEFAULT 'draft',
    route        VARCHAR(20),
    submitted_at TIMESTAMPTZ,
    decided_by   VARCHAR(50),
    decided_at   TIMESTAMPTZ,
    comment      TEXT,
    requester_note TEXT,
    created_at   TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_ar_reviews_cycle_employee UNIQUE (cycle_id, employee_id),
    CONSTRAINT ck_ar_reviews_status CHECK (status IN ('draft', 'pending', 'approved', 'rejected')),
    CONSTRAINT ck_ar_reviews_route CHECK (route IS NULL OR route IN ('dept_head', 'division', 'it_queue', 'bypass'))
);
CREATE INDEX IF NOT EXISTS ix_ar_reviews_status ON ar_reviews (status);

CREATE TABLE IF NOT EXISTS ar_review_lines (
    id           SERIAL PRIMARY KEY,
    review_id    INTEGER NOT NULL REFERENCES ar_reviews (id),
    user_role_id INTEGER NOT NULL REFERENCES ar_user_roles (id),
    decision     VARCHAR(10),
    note         TEXT,
    CONSTRAINT uq_ar_review_lines_review_user_role UNIQUE (review_id, user_role_id),
    CONSTRAINT ck_ar_review_lines_decision CHECK (decision IS NULL OR decision IN ('keep', 'remove'))
);
CREATE INDEX IF NOT EXISTS ix_ar_review_lines_user_role_id ON ar_review_lines (user_role_id);

CREATE TABLE IF NOT EXISTS ar_review_line_items (
    review_line_id INTEGER NOT NULL REFERENCES ar_review_lines (id) ON DELETE CASCADE,
    role_item_id   INTEGER NOT NULL REFERENCES ar_role_items (id),
    PRIMARY KEY (review_line_id, role_item_id)
);
CREATE INDEX IF NOT EXISTS ix_ar_review_line_items_role_item_id ON ar_review_line_items (role_item_id);

CREATE TABLE IF NOT EXISTS ar_review_approvers (
    review_id   INTEGER     NOT NULL REFERENCES ar_reviews (id),
    employee_id VARCHAR(50) NOT NULL,
    mailed_at   TIMESTAMPTZ,
    PRIMARY KEY (review_id, employee_id)
);
CREATE INDEX IF NOT EXISTS ix_ar_review_approvers_employee_id ON ar_review_approvers (employee_id);

INSERT INTO ar_systems (code, name, is_active) VALUES
    ('ATMS',     'ATMS',            TRUE),
    ('WINSPEED', 'Winspeed',        TRUE),
    ('EMAIL',    'Email Group',     TRUE)
ON CONFLICT (code) DO NOTHING;

COMMIT;
