-- เบิกเงินสดย่อย (petty cash) — spec 2026-10-12
CREATE TABLE IF NOT EXISTS fin_petty_funds (
  id                 serial PRIMARY KEY,
  branch_code        varchar(4)  NOT NULL,
  division_code      varchar(8)  NOT NULL,
  holder_employee_id varchar(50) NOT NULL,
  fund_limit         numeric(12,2),
  is_active          boolean     NOT NULL DEFAULT true,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  updated_by         varchar(50)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_fin_petty_funds_active
  ON fin_petty_funds (branch_code, division_code) WHERE is_active;
CREATE INDEX IF NOT EXISTS ix_fin_petty_funds_holder ON fin_petty_funds (holder_employee_id);

CREATE TABLE IF NOT EXISTS fin_petty_expense_types (
  code       varchar(20) PRIMARY KEY,
  name_th    varchar(120) NOT NULL,
  atms_sku   varchar(20),
  sort_order int NOT NULL DEFAULT 0,
  is_active  boolean NOT NULL DEFAULT true,
  spec       jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by varchar(50)
);

CREATE TABLE IF NOT EXISTS fin_petty_batches (
  id           serial PRIMARY KEY,
  fund_id      int NOT NULL REFERENCES fin_petty_funds(id),
  batch_no     varchar(30) NOT NULL UNIQUE,
  total_amount numeric(12,2) NOT NULL DEFAULT 0,
  remark       text,
  closed_by    varchar(50),
  closed_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fin_petty_claims (
  id                  serial PRIMARY KEY,
  form_id             varchar(80) NOT NULL UNIQUE,
  fund_id             int NOT NULL REFERENCES fin_petty_funds(id),
  expense_type        varchar(20) NOT NULL REFERENCES fin_petty_expense_types(code),
  expense_date        date NOT NULL,
  period_from         date,
  period_to           date,
  driver_employee_id  varchar(50),
  driver_name         varchar(150),
  payee_name          varchar(150),
  amount              numeric(12,2) NOT NULL DEFAULT 0,
  detail              jsonb NOT NULL DEFAULT '{}'::jsonb,
  dd_no               varchar(50),
  dd_snapshot         jsonb,
  receipt_no          varchar(50),
  wht_applicable      boolean NOT NULL DEFAULT false,
  created_by          varchar(50) NOT NULL,
  source              varchar(10) NOT NULL DEFAULT 'WEB',
  dup_override_reason text,
  return_reason       text,
  approved_by         varchar(50),
  approved_at         timestamptz,
  transfer_date       date,
  transfer_slip_at    timestamptz,
  submitted_to_acc_at timestamptz,
  review_remark       text,
  reviewed_by         varchar(50),
  reviewed_at         timestamptz,
  batch_id            int REFERENCES fin_petty_batches(id),
  status              varchar(30) NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_fin_petty_claims_status CHECK (status IN
    ('PENDING_APPROVAL','RETURNED','APPROVED','TRANSFERRED','AWAITING_REVIEW','SENT_BACK','CLOSED')),
  CONSTRAINT ck_fin_petty_claims_amount CHECK (amount >= 0)
);
CREATE INDEX IF NOT EXISTS ix_fin_petty_claims_fund_status ON fin_petty_claims (fund_id, status);
CREATE INDEX IF NOT EXISTS ix_fin_petty_claims_status     ON fin_petty_claims (status);
CREATE INDEX IF NOT EXISTS ix_fin_petty_claims_created_by ON fin_petty_claims (created_by);

CREATE TABLE IF NOT EXISTS fin_petty_claim_items (
  id          serial PRIMARY KEY,
  claim_id    int NOT NULL REFERENCES fin_petty_claims(id) ON DELETE CASCADE,
  line_no     int NOT NULL,
  event_date  date NOT NULL,
  truck_code  varchar(20),
  truck_plate varchar(20),
  truck_type  varchar(30),
  ref_no      varchar(50),
  qty         numeric(10,2) NOT NULL DEFAULT 1,
  rate        numeric(12,2) NOT NULL,
  amount      numeric(12,2) NOT NULL,
  remark      varchar(255)
);
CREATE INDEX IF NOT EXISTS ix_fin_petty_items_claim ON fin_petty_claim_items (claim_id);
CREATE INDEX IF NOT EXISTS ix_fin_petty_items_date_code  ON fin_petty_claim_items (event_date, truck_code);
CREATE INDEX IF NOT EXISTS ix_fin_petty_items_date_plate ON fin_petty_claim_items (event_date, truck_plate);
-- ด่านซ้ำเข้ม: บัตรชั่งใบนั้นของรถคันนั้น
CREATE INDEX IF NOT EXISTS ix_fin_petty_items_hard_dup
  ON fin_petty_claim_items (ref_no, truck_code, truck_plate);

CREATE TABLE IF NOT EXISTS fin_petty_logs (
  id         serial PRIMARY KEY,
  claim_id   int NOT NULL REFERENCES fin_petty_claims(id) ON DELETE CASCADE,
  action     varchar(30) NOT NULL,
  changes    jsonb,
  remark     text,
  action_by  varchar(50),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_fin_petty_logs_claim ON fin_petty_logs (claim_id);

INSERT INTO fin_petty_expense_types (code, name_th, atms_sku, sort_order, spec) VALUES
('WEIGH', 'ค่าชั่งน้ำหนัก', 'LB00038', 1,
-- spec:WEIGH
'{
  "item_based": true,
  "item_label": "รายการชั่ง",
  "header_fields": [
    {"key":"period_from","label":"ตั้งแต่วันที่","type":"date","required":true,"auto":"min(item.event_date)"},
    {"key":"period_to","label":"ถึงวันที่","type":"date","required":true,"auto":"max(item.event_date)"},
    {"key":"payee_name","label":"จ่ายให้","type":"employee","required":true}
  ],
  "item_fields": [
    {"key":"event_date","label":"วันที่ชั่ง","type":"date","required":true},
    {"key":"truck","label":"รถ","type":"truck","required":true,"search":["code","plate"]},
    {"key":"truck_type","label":"ประเภทรถ","type":"select","options":["โม่ใหญ่","โม่เล็ก"],"required":true,"auto_from":"truck"},
    {"key":"driver_name","label":"พจส.","type":"text","required":false},
    {"key":"ref_no","label":"เลขที่บัตรชั่ง","type":"text","required":true},
    {"key":"rate","label":"ราคา/ครั้ง","type":"money","required":true}
  ],
  "attachments": [
    {"key":"weigh_slip","label":"ใบชั่งน้ำหนัก (สำเนา)","required":true,"max":10},
    {"key":"transfer","label":"หลักฐานโอนเงิน","required":true,"max":3},
    {"key":"other","label":"อื่นๆ","required":false,"max":3}
  ],
  "rules": [
    {"when":"total >= 500","require_field":"dd_no","at":"BEFORE_SUBMIT_TO_ACCOUNTING"},
    {"when":"always","require_attachment":"weigh_slip","at":"BEFORE_APPROVE"},
    {"when":"always","require_attachment":"transfer","at":"BEFORE_APPROVE"}
  ],
  "duplicate_check": {
    "scope": "item",
    "hard": {"keys":["ref_no","truck_code","truck_plate"],"days":365,"action":"block","override":false},
    "soft": {"keys":["event_date","truck"],"days":7,"action":"warn","override":"reason"}
  },
  "print": {"unit":"คัน"},
  "prints": ["PETTY_VOUCHER","RECEIPT_SUBSTITUTE","DD"],
  "atms": {"sku":"LB00038","product_group":"ค่าแรง","unit":"งาน","vat":0}
}'
-- /spec
::jsonb),
('TOLL',   'ค่าทางด่วน',      NULL,       2, '{}'::jsonb),
('TIRE',   'ค่าปะ-เปลี่ยนยาง', 'LB00009',  3, '{}'::jsonb),
('REPAIR', 'ค่าซ่อมฉุกเฉิน',   NULL,       4, '{}'::jsonb),
('TRAVEL', 'ค่าเดินทางคนขับ',  NULL,       5, '{}'::jsonb)
ON CONFLICT (code) DO NOTHING;
