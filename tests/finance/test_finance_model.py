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
    assert "fin_status IN ('PAID','CLEARING_SUBMITTED','SENT_BACK','CLOSED')" in ddl
    assert "NUMERIC(12, 2)" in ddl
