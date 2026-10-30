"""Access Review test fixtures — in-memory SQLite only (never the shared Postgres in .env).

Org used by the tests:
  division 10: departments 1, 2      division 20: department 3      division 30: department 21 (IT → admin)
"""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

from datetime import date  # noqa: E402

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from database import Base, get_db  # noqa: E402
from models.access_review_model import (  # noqa: E402
    ArCycle, ArRole, ArRoleItem, ArSystem, ArUserRole,
)
from models.form_approver_division import FormApproverDivision  # noqa: E402
from models.user_model import Department, Division, Position, PositionLevel, Site, User  # noqa: E402

LEVELS = ["Officer", "Assistant Supervisor", "Supervisor", "Assistant Manager", "Manager", "Senior Manager",
          "Deputy C-level", "C-level", "Chief Executive Officer"]
TABLES = [PositionLevel, Position, Division, Department, Site, User, FormApproverDivision, ArSystem, ArRole,
          ArRoleItem, ArUserRole, ArCycle]


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from models import access_review_model as m
    tables = [t.__table__ for t in TABLES] + [m.ArReview.__table__, m.ArReviewLine.__table__,
                                                m.ArReviewApprover.__table__, m.ArReviewLineItem.__table__,
                                                m.ArReviewLineSnapshot.__table__]
    Base.metadata.create_all(eng, tables=tables)
    yield eng
    eng.dispose()


@pytest.fixture
def Session(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def db(Session):
    s = Session()
    yield s
    s.close()


class Org:
    """Builder for users / roles / user roles inside the test DB."""

    def __init__(self, db):
        self.db = db
        for i, name in enumerate(LEVELS, start=1):
            db.add(PositionLevel(position_level_id=i, level_name=name))
            db.add(Position(position_id=100 + i, position_name_th=f"ตำแหน่ง Lv{i}", position_name_en=f"Pos {i}",
                            position_level_id=i))
        for div in (10, 20, 30):
            db.add(Division(division_id=div, division_th=f"ฝ่าย {div}", division_eng=f"Div {div}"))
        for dept, div in ((1, 10), (2, 10), (3, 20), (21, 30)):
            db.add(Department(department_id=dept, department_name_th=f"แผนก {dept}",
                              department_name_en=f"Dept {dept}", division_id=div))
        self.systems = {}
        for code in ("ATMS", "WINSPEED", "EMAIL"):
            s = ArSystem(code=code, name=code.title(), is_active=True)
            db.add(s)
            self.systems[code] = s
        db.commit()
        self._n = 0

    def user(self, eid, level, dept, status="Active", username=None, email=None):
        self._n += 1
        u = User(id=self._n, username=username or f"user{eid}", password_hash="x", firstname=f"First{eid}",
                 lastname="Last", employee_id=eid, department_id=dept, position_id=100 + level if level else None,
                 email=email or f"user{eid}@example.com", employee_status=status)
        self.db.add(u)
        self.db.commit()
        return u

    def division_approver(self, eid, division_id, active=True):
        self.db.add(FormApproverDivision(employee_id=eid, division_id=division_id, is_active=active))
        self.db.commit()

    def role(self, code, name, items=()):
        r = ArRole(system_id=self.systems[code].id, name=name, is_active=True)
        self.db.add(r)
        self.db.flush()
        for cat, item, perm in items:
            self.db.add(ArRoleItem(role_id=r.id, category=cat, item=item, permission=perm))
        self.db.commit()
        return r

    def grant(self, eid, code, role, account_name=None):
        """code is kept for readability — the system comes from the role."""
        assert role.system_id == self.systems[code].id
        link = ArUserRole(employee_id=eid, role_id=role.id, account_name=account_name, is_active=True)
        self.db.add(link)
        self.db.commit()
        return link

    def cycle(self, name="H2-2569", is_open=True, start=date(2026, 7, 1), end=date(2026, 12, 31)):
        c = ArCycle(name=name, start_date=start, end_date=end, is_open=is_open)
        self.db.add(c)
        self.db.commit()
        return c


@pytest.fixture
def org(db):
    return Org(db)


@pytest.fixture
def client(Session):
    from routes.access_review.access_review_routes import router

    app = FastAPI()
    app.include_router(router)

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def h(eid):
    return {"X-Employee-Id": eid}
