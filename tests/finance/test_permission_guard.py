"""ด่านสิทธิ์ที่ endpoint — กันจริงไม่ใช่แค่ซ่อนปุ่ม"""
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

from routes.finance import advance_routes as r  # noqa: E402
from services.finance import permission_logic as pl  # noqa: E402
from services.finance import permission_repo  # noqa: E402

BOW, NONG, OUTSIDER = "610197", "660548", "570245"
PEOPLE = {
    BOW: SimpleNamespace(employee_id=BOW, department_id=4),
    NONG: SimpleNamespace(employee_id=NONG, department_id=6),
    OUTSIDER: SimpleNamespace(employee_id=OUTSIDER, department_id=12),
}


class FakeDB:
    def query(self, _model):
        return self

    def filter(self, criterion):
        self._wanted = criterion.right.value
        return self

    def first(self):
        return PEOPLE.get(self._wanted)


@pytest.fixture
def grants(monkeypatch):
    table = {pl.PERM_PAY: {BOW}, pl.PERM_CLEARING_REVIEW: {NONG}}
    monkeypatch.setattr(permission_repo, "load_grants", lambda db, fresh=False: table)
    return table


def test_เจ้าของสิทธิ์ผ่านด่าน(grants):
    assert r.require_permission(FakeDB(), BOW, pl.PERM_PAY).employee_id == BOW


def test_คนการเงินที่ไม่มีสิทธิ์นั้นโดนปฏิเสธ_403(grants):
    with pytest.raises(HTTPException) as exc:
        r.require_permission(FakeDB(), NONG, pl.PERM_PAY)
    assert exc.value.status_code == 403
    assert "จ่ายเงิน" in exc.value.detail


def test_คนนอกการเงินโดนปฏิเสธก่อนถึงด่านสิทธิ์(grants):
    with pytest.raises(HTTPException) as exc:
        r.require_permission(FakeDB(), OUTSIDER, pl.PERM_PAY)
    assert exc.value.status_code == 403
    assert exc.value.detail == "เฉพาะฝ่ายการเงินเท่านั้น"


def test_สิทธิ์ที่ยังไม่ตั้งค่ายังทำได้ตามกติกาเดิม(grants):
    # ยังไม่มีใครถือสิทธิ์ปิดรายการ คนการเงินจึงยังปิดได้
    assert r.require_permission(FakeDB(), BOW, pl.PERM_CLOSE).employee_id == BOW


def test_ด่านอ่านสิทธิ์สดเสมอ_ถอดสิทธิ์แล้วมีผลทันที(monkeypatch):
    calls = []

    def load(db, fresh=False):
        calls.append(fresh)
        return {pl.PERM_PAY: {BOW}}

    monkeypatch.setattr(permission_repo, "load_grants", load)
    r.require_permission(FakeDB(), BOW, pl.PERM_PAY)
    assert calls == [True]
