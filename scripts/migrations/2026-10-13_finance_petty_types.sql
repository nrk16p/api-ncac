-- ประเภทการเบิกที่เหลือ 4 แบบ — แก้ต่อได้ที่หน้า master ไม่ต้องแก้โค้ด
-- คอมเมนต์ -- spec:CODE / -- /spec มีไว้ให้ tests/finance/test_petty_types_spec.py อ่านของจริง
UPDATE fin_petty_expense_types SET spec =
-- spec:TOLL
'{
  "item_based": false,
  "header_fields": [
    {"key":"event_date","label":"วันที่ใช้ทาง","type":"date","required":true},
    {"key":"truck","label":"รถ","type":"truck","required":true,"search":["code","plate"]},
    {"key":"driver","label":"พจส.","type":"employee","required":true},
    {"key":"rate","label":"จำนวนเงิน","type":"money","required":true},
    {"key":"receipt_no","label":"เลขที่ใบเสร็จ","type":"text","required":false}
  ],
  "attachments": [
    {"key":"receipt","label":"ใบเสร็จค่าทางด่วน","required":true,"max":10},
    {"key":"transfer","label":"หลักฐานโอนเงิน","required":true,"max":3}
  ],
  "rules": [
    {"when":"total >= 500","require_field":"dd_no","at":"BEFORE_SUBMIT_TO_ACCOUNTING"},
    {"when":"always","require_attachment":"receipt","at":"BEFORE_APPROVE"}
  ],
  "duplicate_check": {
    "scope":"item",
    "hard":{"keys":["ref_no","truck_code","truck_plate"],"days":365,"action":"block","override":false},
    "soft":{"keys":["event_date","truck"],"days":7,"action":"warn","override":"reason"}
  },
  "prints": ["PETTY_VOUCHER","DD"]
}'
-- /spec
::jsonb WHERE code = 'TOLL';

UPDATE fin_petty_expense_types SET spec =
-- spec:TIRE
'{
  "item_based": true,
  "item_label": "รายการปะ/เปลี่ยนยาง",
  "item_fields": [
    {"key":"event_date","label":"วันที่","type":"date","required":true},
    {"key":"truck","label":"รถ","type":"truck","required":true,"search":["code","plate"]},
    {"key":"ref_no","label":"เลขที่ใบเสร็จ/บิล","type":"text","required":true},
    {"key":"remark","label":"รายการที่ทำ","type":"text","required":true},
    {"key":"rate","label":"จำนวนเงิน","type":"money","required":true}
  ],
  "attachments": [
    {"key":"receipt","label":"ใบเสร็จ/บิลเงินสด","required":true,"max":10},
    {"key":"photo","label":"รูปยางที่เปลี่ยน","required":false,"max":10},
    {"key":"transfer","label":"หลักฐานโอนเงิน","required":true,"max":3}
  ],
  "rules": [
    {"when":"total >= 500","require_field":"dd_no","at":"BEFORE_SUBMIT_TO_ACCOUNTING"},
    {"when":"always","require_attachment":"receipt","at":"BEFORE_APPROVE"}
  ],
  "duplicate_check": {
    "scope":"item",
    "hard":{"keys":["ref_no","truck_code","truck_plate"],"days":365,"action":"block","override":false},
    "soft":{"keys":["event_date","truck"],"days":7,"action":"warn","override":"reason"}
  },
  "prints": ["PETTY_VOUCHER","DD"],
  "atms": {"sku":"LB00009","product_group":"ค่าแรง","unit":"งาน","vat":0}
}'
-- /spec
::jsonb WHERE code = 'TIRE';

UPDATE fin_petty_expense_types SET spec =
-- spec:REPAIR
'{
  "item_based": true,
  "item_label": "รายการซ่อม",
  "item_fields": [
    {"key":"event_date","label":"วันที่ซ่อม","type":"date","required":true},
    {"key":"truck","label":"รถ","type":"truck","required":true,"search":["code","plate"]},
    {"key":"ref_no","label":"เลขที่ใบเสร็จ/ใบแจ้งซ่อม","type":"text","required":true},
    {"key":"remark","label":"อาการ/งานที่ทำ","type":"text","required":true},
    {"key":"rate","label":"จำนวนเงิน","type":"money","required":true}
  ],
  "attachments": [
    {"key":"receipt","label":"ใบเสร็จ/บิลเงินสด","required":true,"max":10},
    {"key":"photo","label":"รูปหน้างาน","required":true,"max":10},
    {"key":"transfer","label":"หลักฐานโอนเงิน","required":true,"max":3}
  ],
  "rules": [
    {"when":"total >= 500","require_field":"dd_no","at":"BEFORE_SUBMIT_TO_ACCOUNTING"},
    {"when":"always","require_attachment":"receipt","at":"BEFORE_APPROVE"},
    {"when":"always","require_attachment":"photo","at":"BEFORE_APPROVE"}
  ],
  "duplicate_check": {
    "scope":"item",
    "hard":{"keys":["ref_no","truck_code","truck_plate"],"days":365,"action":"block","override":false},
    "soft":{"keys":["event_date","truck"],"days":7,"action":"warn","override":"reason"}
  },
  "prints": ["PETTY_VOUCHER","DD"]
}'
-- /spec
::jsonb WHERE code = 'REPAIR';

UPDATE fin_petty_expense_types SET spec =
-- spec:TRAVEL
'{
  "item_based": false,
  "header_fields": [
    {"key":"event_date","label":"วันที่เดินทาง","type":"date","required":true},
    {"key":"driver","label":"พจส.","type":"employee","required":true},
    {"key":"remark","label":"เส้นทาง/เหตุผล","type":"text","required":true},
    {"key":"rate","label":"จำนวนเงิน","type":"money","required":true}
  ],
  "attachments": [
    {"key":"receipt","label":"ใบเสร็จ (ถ้ามี)","required":false,"max":10},
    {"key":"transfer","label":"หลักฐานโอนเงิน","required":true,"max":3}
  ],
  "rules": [
    {"when":"total >= 500","require_field":"dd_no","at":"BEFORE_SUBMIT_TO_ACCOUNTING"},
    {"when":"always","require_attachment":"transfer","at":"BEFORE_APPROVE"}
  ],
  "duplicate_check": {
    "scope":"item",
    "hard":{"keys":["ref_no","truck_code","truck_plate"],"days":365,"action":"block","override":false},
    "soft":{"keys":["event_date","driver"],"days":7,"action":"warn","override":"reason"}
  },
  "prints": ["PETTY_VOUCHER","RECEIPT_SUBSTITUTE","DD"]
}'
-- /spec
::jsonb WHERE code = 'TRAVEL';
