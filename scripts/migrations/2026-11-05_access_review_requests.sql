-- Access Review: "ต้องการเพิ่มสิทธิ์" — items the requester asks to ADD per review line (2026-11-05).
-- Additive only, idempotent.
--
-- * ar_review_line_requests   a menu the role doesn't have, picked from another role of the same system
--                             (GET /access-review/catalog). Stored as that item's id, like ar_review_line_items.
--
-- main.py's create_all() also creates this table on boot; running this file first is the explicit path.
-- The importer / admin item delete treat requested items as referenced (soft-delete, never hard-delete).
BEGIN;

CREATE TABLE IF NOT EXISTS ar_review_line_requests (
    review_line_id INTEGER NOT NULL REFERENCES ar_review_lines (id) ON DELETE CASCADE,
    role_item_id   INTEGER NOT NULL REFERENCES ar_role_items (id),
    PRIMARY KEY (review_line_id, role_item_id)
);
CREATE INDEX IF NOT EXISTS ix_ar_review_line_requests_role_item_id ON ar_review_line_requests (role_item_id);

COMMIT;
