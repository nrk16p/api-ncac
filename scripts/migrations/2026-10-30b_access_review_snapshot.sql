-- Access Review: "before" snapshot per review line (2026-10-30) — additive only, idempotent.
--
-- * ar_review_line_snapshot   every active item the role had when the review was submitted, so the admin
--                             before → after view and the review dialog keep showing what the requester saw
--                             even after an import adds or removes items. Reviews submitted before this table
--                             existed have no snapshot and fall back to the role's live items.
--
-- main.py's create_all() also creates this table on boot; running this file first is the explicit path.
-- The importer / admin item delete treat snapshotted items as referenced (soft-delete, never hard-delete).
BEGIN;

CREATE TABLE IF NOT EXISTS ar_review_line_snapshot (
    review_line_id INTEGER NOT NULL REFERENCES ar_review_lines (id) ON DELETE CASCADE,
    role_item_id   INTEGER NOT NULL REFERENCES ar_role_items (id),
    PRIMARY KEY (review_line_id, role_item_id)
);
CREATE INDEX IF NOT EXISTS ix_ar_review_line_snapshot_role_item_id ON ar_review_line_snapshot (role_item_id);

COMMIT;
