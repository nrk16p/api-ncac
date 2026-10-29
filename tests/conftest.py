import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

import pytest


@pytest.fixture
def db_session():
    """sqlite in-memory สำหรับเทสต์ที่ต้องมี session จริง — ไม่แตะ Postgres

    สร้างแค่ตารางที่งาน petty ใช้ (metadata ทั้งก้อนสร้างบน sqlite ไม่ได้ เพราะตารางเก่า
    บางตัวใช้ JSONB ตรง ๆ) · ตารางของ petty เองใช้ JSON ที่ variant เป็น JSONB บน Postgres
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from database import Base
    import models.finance_model as fin
    import models.finance_petty_model as petty
    import models.master_model as master
    import models.user_model as users

    engine = create_engine("sqlite://")
    tables = [
        master.FormSubmission.__table__, master.FormSequence.__table__,
        fin.FinAdvance.__table__,
        # บัญชีผู้รับเงินของเจ้าของเงินสด (กล่องจ่ายเงินของรอบ) · ตาราง log ใช้ JSONB สร้างบน sqlite ไม่ได้ ไม่ต้องใช้
        fin.FinPayeeAccountRequest.__table__, fin.FinPayeeAccount.__table__,
        users.User.__table__,  # petty_repo.employee_email อ่านอีเมลจากตารางนี้
        petty.FinPettyFund.__table__, petty.FinPettyExpenseType.__table__,
        petty.FinPettyBatch.__table__, petty.FinPettyClaim.__table__,
        petty.FinPettyClaimItem.__table__, petty.FinPettyLog.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def seq_stub(monkeypatch):
    """นับเลขเอกสารในหน่วยความจำ — `_locked_sequence` ตัวจริงใช้ pg_insert + SELECT FOR UPDATE
    ซึ่ง sqlite ไม่รองรับ · การกันเลขชนเป็นหน้าที่ของ Postgres และมีเทสต์ของ ADV คุมอยู่แล้ว
    ที่นี่ทดสอบ *รูปแบบเลข* และการที่ repo เรียกใช้จริง
    """
    import routes.forms.form_submission_routes as mod

    counters: dict[tuple[str, int], int] = {}

    class _Seq:
        def __init__(self, key):
            self.key = key

        @property
        def last_number(self):
            return counters.get(self.key, 0)

        @last_number.setter
        def last_number(self, value):
            counters[self.key] = value

    monkeypatch.setattr(mod, "_locked_sequence", lambda db, code, year: _Seq((code, year)))
    return counters


@pytest.fixture
def frozen_bkk(monkeypatch):
    """ตรึงเวลาไทยไว้ 12/10/2026 เพื่อให้เลขเอกสารออกเป็น 2610"""
    from datetime import datetime

    import routes.forms.form_submission_routes as mod
    monkeypatch.setattr(mod, "_now_bkk", lambda: datetime(2026, 10, 12, 9, 0, 0))
