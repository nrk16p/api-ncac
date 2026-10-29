-- Access Review: ar_accounts + ar_account_roles → ar_user_roles (2026-10-29b)
--
-- For a DB that already ran the earlier draft of 2026-10-29_access_review.sql (prod). Fresh DBs: the rewritten
-- 2026-10-29_access_review.sql already has the final schema — do not run this file there (it would just no-op
-- the drops and create the same tables).
--
-- DESTRUCTIVE: drops ar_review_lines, ar_account_roles, ar_accounts. Refuses to run if ar_reviews has any row,
-- so no review history can be lost. ar_systems / ar_roles / ar_role_items / ar_cycles / ar_reviews /
-- ar_review_approvers are kept. Re-import the user↔role data afterwards:
--   python scripts/access_review_import.py ATMS "Role ATMS.xlsx" --commit   (and WINSPEED, EMAIL)
BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM ar_reviews) THEN
        RAISE EXCEPTION 'ar_reviews has rows — refusing to drop ar_review_lines / ar_account_roles / ar_accounts';
    END IF;
END
$$;

DROP TABLE IF EXISTS ar_review_lines;
DROP TABLE IF EXISTS ar_account_roles;
DROP TABLE IF EXISTS ar_accounts;

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

COMMIT;
