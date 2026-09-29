-- Finance Advance v2b (2026-09-29) — ตีกลับไปตั้งเบิกใหม่ at รอจ่าย
-- Run once in DBeaver against ncacdb (safe to re-run). Widens ck_fin_advances_fin_status to allow 'VOUCHER_REJECTED'.
BEGIN;

ALTER TABLE fin_advances DROP CONSTRAINT IF EXISTS ck_fin_advances_fin_status;
ALTER TABLE fin_advances ADD CONSTRAINT ck_fin_advances_fin_status
    CHECK (fin_status IN ('VOUCHERED','VOUCHER_REJECTED','PAID','CLEARING_SUBMITTED','SENT_BACK','CLOSED'));

COMMIT;

-- verify (read-only)
SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_fin_advances_fin_status';
