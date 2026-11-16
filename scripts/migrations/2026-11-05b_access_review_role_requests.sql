-- Access Review: "ต้องการเพิ่มอีเมลกลุ่ม" — whole roles (Email groups) the requester asks for (2026-11-05).
-- Additive only, idempotent.
--
-- * ar_review_role_requests   a role the requester does not hold yet (no ar_user_roles row), so it is stored per
--                             review rather than per line. Picked from GET /access-review/catalog/roles.
--
-- main.py's create_all() also creates this table on boot; running this file first is the explicit path.
BEGIN;

CREATE TABLE IF NOT EXISTS ar_review_role_requests (
    review_id INTEGER NOT NULL REFERENCES ar_reviews (id) ON DELETE CASCADE,
    role_id   INTEGER NOT NULL REFERENCES ar_roles (id),
    PRIMARY KEY (review_id, role_id)
);
CREATE INDEX IF NOT EXISTS ix_ar_review_role_requests_role_id ON ar_review_role_requests (role_id);

COMMIT;
