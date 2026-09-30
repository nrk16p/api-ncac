-- Finance Advance v2c (2026-09-30) — show the full centre name after the code in the ADV
-- "ค่าใช้จ่ายรายศูนย์" dropdown. Only option_label changes; option_value stays the code, so the
-- stored values, the print-form ศูนย์ mapping and the list column are unaffected.
-- Run once in DBeaver against ncacdb (safe to re-run).
BEGIN;

UPDATE form_question_options o
SET option_label = v.label
FROM form_questions q
JOIN form_masters m ON m.id = q.form_master_id AND m.form_code = 'ADV' AND m.is_latest = true,
     (VALUES ('ศลบ', 'ศลบ - ศูนย์ลาดกระบัง'),
             ('สกท', 'สกท - สำนักงานกรุงเทพ (สำนักงานใหญ่)'),
             ('สสบ', 'สสบ - สำนักงานสระบุรี'),
             ('ศรย', 'ศรย - ศูนย์ระยอง'),
             ('ศขก', 'ศขก - ศูนย์ขอนแก่น'),
             ('ศบก', 'ศบก - ศูนย์บางปะกง')) AS v(code, label)
WHERE o.question_id = q.id
  AND q.question_name = 'adv_cost_center'
  AND o.option_value = v.code;

COMMIT;

-- verify (read-only): expect 6 rows, value = code, label = code + full name
SELECT o.option_value, o.option_label
FROM form_question_options o
JOIN form_questions q ON q.id = o.question_id AND q.question_name = 'adv_cost_center'
JOIN form_masters m ON m.id = q.form_master_id AND m.form_code = 'ADV' AND m.is_latest = true
ORDER BY o.sort_order;
