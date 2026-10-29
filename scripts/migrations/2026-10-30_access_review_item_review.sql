-- Access Review: item-level review (2026-10-30) — additive only, idempotent.
--
-- For a DB that already has the ar_* tables (prod, after 2026-10-29 + 2026-10-29b). Fresh DBs: the current
-- 2026-10-29_access_review.sql already contains these changes (running this afterwards is a no-op).
--
-- * ar_reviews.requester_note      ข้อเสนอแนะ (ผู้แจ้ง)
-- * ar_review_line_items           the items the requester UNTICKED (ticked items are not stored)
-- * ar_role_items.is_active        the importer no longer deletes an item referenced by ar_review_line_items:
--                                  an item gone from the new file is hard-deleted if unreferenced, otherwise
--                                  set is_active = false (hidden from new reviews, kept for history)
BEGIN;

ALTER TABLE ar_reviews ADD COLUMN IF NOT EXISTS requester_note TEXT;
ALTER TABLE ar_role_items ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

CREATE TABLE IF NOT EXISTS ar_review_line_items (
    review_line_id INTEGER NOT NULL REFERENCES ar_review_lines (id) ON DELETE CASCADE,
    role_item_id   INTEGER NOT NULL REFERENCES ar_role_items (id),
    PRIMARY KEY (review_line_id, role_item_id)
);
CREATE INDEX IF NOT EXISTS ix_ar_review_line_items_role_item_id ON ar_review_line_items (role_item_id);

COMMIT;
