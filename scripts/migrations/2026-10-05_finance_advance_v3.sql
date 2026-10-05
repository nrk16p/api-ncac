-- Finance Advance v3 (2026-10-05) — ตีกลับให้ผู้เบิกแก้ไข (spec 2026-10-05-finance-advance-v3-design.md §6)
-- Run once in DBeaver against ncacdb (safe to re-run: DROP IF EXISTS + ADD in one transaction).
-- Widens ck_fin_advances_fin_status with 'RETURNED' (waiting for the requester) and 'RESUBMITTED'
-- (the requester sent it again). Every value allowed before (prod 2026-10-05:
-- VOUCHERED, VOUCHER_REJECTED, PAID, CLEARING_SUBMITTED, SENT_BACK, CLOSED) stays allowed, so existing rows pass.
-- form_approval_logs.action has no CHECK: the RETURNED / RESUBMITTED markers (level_no 0) need no change.
BEGIN;

ALTER TABLE fin_advances DROP CONSTRAINT IF EXISTS ck_fin_advances_fin_status;
ALTER TABLE fin_advances ADD CONSTRAINT ck_fin_advances_fin_status
    CHECK (fin_status IN ('VOUCHERED','VOUCHER_REJECTED','RETURNED','RESUBMITTED','PAID','CLEARING_SUBMITTED','SENT_BACK','CLOSED'));

COMMIT;

-- verify (read-only)
SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_fin_advances_fin_status';
SELECT fin_status, count(*) AS rows FROM fin_advances GROUP BY fin_status ORDER BY fin_status;
