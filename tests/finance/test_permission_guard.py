"""ด่านสิทธิ์ที่ endpoint — กันจริงไม่ใช่แค่ซ่อนปุ่ม"""
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

from routes.finance import advance_routes as r  # noqa: E402
from services.finance import permission_logic as pl  # noqa: E402

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


def test_การเงินบันทึกจ่ายได้():
    assert r.require_permission(FakeDB(), BOW, pl.PERM_PAY).employee_id == BOW


def test_บัญชีบันทึกจ่ายไม่ได้_403():
    with pytest.raises(HTTPException) as exc:
        r.require_permission(FakeDB(), NONG, pl.PERM_PAY)
    assert exc.value.status_code == 403
    assert exc.value.detail == "คุณไม่มีสิทธิ์จ่ายเงิน ปุ่มนี้สำหรับแผนกการเงิน"


def test_การเงินปิดรายการไม่ได้_ปุ่มอื่นเป็นของบัญชี():
    with pytest.raises(HTTPException) as exc:
        r.require_permission(FakeDB(), BOW, pl.PERM_CLOSE)
    assert exc.value.status_code == 403
    assert r.require_permission(FakeDB(), NONG, pl.PERM_CLOSE).employee_id == NONG


def test_คนนอกการเงินโดนปฏิเสธก่อนถึงด่านสิทธิ์():
    with pytest.raises(HTTPException) as exc:
        r.require_permission(FakeDB(), OUTSIDER, pl.PERM_PAY)
    assert exc.value.status_code == 403
    assert exc.value.detail == "เฉพาะฝ่ายการเงินเท่านั้น"
