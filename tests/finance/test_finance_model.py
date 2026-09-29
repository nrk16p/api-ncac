import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

from sqlalchemy.dialects import postgresql  # noqa: E402
from sqlalchemy.schema import CreateTable  # noqa: E402


def test_tables_registered_before_create_all():
    import models  # noqa: F401  (main.py imports models, then calls create_all)
    from database import Base
    for table in ("fin_accounts", "fin_advances", "fin_advance_logs"):
        assert table in Base.metadata.tables


def test_fin_advances_constraints():
    from models.finance_model import FinAdvance
    ddl = str(CreateTable(FinAdvance.__table__).compile(dialect=postgresql.dialect()))
    assert "UNIQUE (submission_id)" in ddl
    assert "UNIQUE (form_id)" in ddl
    assert "amount_paid >= 0" in ddl
    assert "fin_status IN ('VOUCHERED','PAID','CLEARING_SUBMITTED','SENT_BACK','CLOSED')" in ddl
    assert "amount_paid NUMERIC(12, 2)," in ddl
    assert "transfer_date DATE," in ddl
    assert "NUMERIC(12, 2)" in ddl


def test_fin_approval_tiers_matches_sql():
    import models  # noqa: F401
    from database import Base
    from models.finance_model import FinApprovalTier
    assert "fin_approval_tiers" in Base.metadata.tables
    ddl = str(CreateTable(FinApprovalTier.__table__).compile(dialect=postgresql.dialect()))
    assert "clause VARCHAR(10) NOT NULL" in ddl
    assert "amount_max NUMERIC(14, 2)" in ddl
    assert "min_level BETWEEN 1 AND 20" in ddl
    assert "approver_label VARCHAR(120) NOT NULL" in ddl
    assert "PRIMARY KEY (clause)" in ddl
    sql = open(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "migrations",
                            "2026-09-29_finance_advance_v2.sql"), encoding="utf-8").read()
    for fragment in ("CREATE TABLE IF NOT EXISTS fin_approval_tiers", "numeric(14,2)",
                     "CHECK (min_level BETWEEN 1 AND 20)", "'6.1'", "ON CONFLICT (clause) DO NOTHING",
                     "UPDATE form_approval_rules SET is_active = false", "'adv_account_no'",
                     "ALTER COLUMN amount_paid    DROP NOT NULL", "'VOUCHERED'"):
        assert fragment in sql
