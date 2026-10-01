-- Finance Advance v2d — clearing line items (A/B/C/D/E). Run in DBeaver (one transaction, idempotent).
-- Run BEFORE starting the BE that contains this change. amount_actual stays on fin_advances (= sum of net_amount).
BEGIN;

CREATE TABLE IF NOT EXISTS fin_advance_clear_items (
    id                serial        PRIMARY KEY,
    advance_id        integer       NOT NULL REFERENCES fin_advances(id) ON DELETE CASCADE,
    line_no           integer       NOT NULL,
    expense_date      date          NOT NULL,
    vehicle           varchar(50),
    has_receipt       boolean       NOT NULL DEFAULT true,
    description       varchar(255)  NOT NULL,
    amount_before_vat numeric(12,2) NOT NULL,
    vat_amount        numeric(12,2) NOT NULL,
    total_amount      numeric(12,2) NOT NULL,
    wht_amount        numeric(12,2) NOT NULL,
    net_amount        numeric(12,2) NOT NULL,
    created_at        timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT ck_fin_advance_clear_items_amounts CHECK (amount_before_vat >= 0 AND vat_amount >= 0 AND total_amount >= 0 AND wht_amount >= 0 AND net_amount >= 0)
);

CREATE INDEX IF NOT EXISTS ix_fin_advance_clear_items_advance_id ON fin_advance_clear_items (advance_id);

COMMIT;

-- verify (read-only)
SELECT column_name, data_type, is_nullable FROM information_schema.columns
WHERE table_name = 'fin_advance_clear_items' ORDER BY ordinal_position;
