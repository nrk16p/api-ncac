-- form_approver_departments → form_approver_divisions (2026-10-25)
--
-- The approver responsibility table now maps an approver to a division (ฝ่าย):
-- the approver may approve any requester whose department belongs to that division.
-- Used by the IT-form approval routes and by finance ADV approval (approval_repo.load_mappings).
--
-- Step 1 (expand, run BEFORE deploying the code): create the new table and copy
-- the active department mappings as their departments' divisions. The old code
-- keeps reading form_approver_departments, so this is safe to run any time.
BEGIN;

CREATE TABLE IF NOT EXISTS form_approver_divisions (
    id          SERIAL PRIMARY KEY,
    employee_id VARCHAR NOT NULL,
    division_id INTEGER NOT NULL,
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMP DEFAULT now(),
    CONSTRAINT uq_approver_division UNIQUE (employee_id, division_id)
);

-- mappings to a department that no longer exists are dropped (no division to map to)
INSERT INTO form_approver_divisions (employee_id, division_id, is_active)
SELECT DISTINCT f.employee_id, d.division_id, TRUE
FROM form_approver_departments f
JOIN departments d ON d.department_id = f.department_id
WHERE f.is_active
ON CONFLICT (employee_id, division_id) DO NOTHING;

COMMIT;

-- Step 2 (contract, only after the new code is live and verified):
-- DROP TABLE form_approver_departments;
