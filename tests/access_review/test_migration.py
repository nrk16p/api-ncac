"""Migration files vs the SQLAlchemy models (text checks — no database)."""
import re
from pathlib import Path

from models import access_review_model as m

MIGRATIONS = Path(__file__).resolve().parents[2] / "scripts" / "migrations"
FULL = (MIGRATIONS / "2026-10-29_access_review.sql").read_text(encoding="utf-8")
FIX = (MIGRATIONS / "2026-10-29b_access_review_user_roles.sql").read_text(encoding="utf-8")
MODEL_TABLES = {t.__tablename__ for t in (m.ArSystem, m.ArRole, m.ArRoleItem, m.ArUserRole, m.ArCycle, m.ArReview,
                                          m.ArReviewLine, m.ArReviewLineItem, m.ArReviewLineSnapshot,
                                          m.ArReviewApprover)}


def _tables(sql):
    return set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql))


def _columns(sql, table):
    body = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);", sql, re.S).group(1)
    return {line.split()[0] for line in body.splitlines()
            if line.strip() and not line.strip().startswith(("CONSTRAINT", "PRIMARY KEY"))}


def test_full_migration_has_the_10_model_tables_and_columns():
    assert _tables(FULL) == MODEL_TABLES
    for t in MODEL_TABLES:
        model_cols = set(m.Base.metadata.tables[t].columns.keys())
        assert _columns(FULL, t) == model_cols, t
    assert "ar_accounts" not in _tables(FULL) and "ar_account_roles" not in _tables(FULL)


def test_fix_migration_guards_reviews_and_rebuilds_user_roles():
    assert FIX.strip().startswith("--") and "BEGIN;" in FIX and FIX.rstrip().endswith("COMMIT;")
    guard = FIX.index("RAISE EXCEPTION")
    assert "IF EXISTS (SELECT 1 FROM ar_reviews)" in FIX[:guard]
    for drop in ("DROP TABLE IF EXISTS ar_review_lines;", "DROP TABLE IF EXISTS ar_account_roles;",
                 "DROP TABLE IF EXISTS ar_accounts;"):
        assert guard < FIX.index(drop)
    assert _tables(FIX) == {"ar_user_roles", "ar_review_lines"}
    for t in ("ar_user_roles", "ar_review_lines"):
        assert _columns(FIX, t) == set(m.Base.metadata.tables[t].columns.keys())


ITEM = (MIGRATIONS / "2026-10-30_access_review_item_review.sql").read_text(encoding="utf-8")


def test_item_review_migration_is_additive():
    assert "BEGIN;" in ITEM and ITEM.rstrip().endswith("COMMIT;")
    assert "DROP" not in ITEM.upper().replace("DROPPED", "")
    assert "ALTER TABLE ar_reviews ADD COLUMN IF NOT EXISTS requester_note TEXT;" in ITEM
    assert "ALTER TABLE ar_role_items ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;" in ITEM
    assert _tables(ITEM) == {"ar_review_line_items"}
    assert _columns(ITEM, "ar_review_line_items") == set(m.Base.metadata.tables["ar_review_line_items"].columns.keys())
    assert "ON DELETE CASCADE" in ITEM


SNAP = (MIGRATIONS / "2026-10-30b_access_review_snapshot.sql").read_text(encoding="utf-8")


def test_snapshot_migration_is_additive():
    assert "BEGIN;" in SNAP and SNAP.rstrip().endswith("COMMIT;")
    assert "DROP" not in SNAP.upper() and "ALTER" not in SNAP.upper()
    assert _tables(SNAP) == {"ar_review_line_snapshot"}
    assert _columns(SNAP, "ar_review_line_snapshot") == set(
        m.Base.metadata.tables["ar_review_line_snapshot"].columns.keys())
    assert "ON DELETE CASCADE" in SNAP
