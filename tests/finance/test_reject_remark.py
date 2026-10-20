# -*- coding: utf-8 -*-
"""ไม่อนุมัติใบเบิก Advance ต้องมีเหตุผล — กันที่หลังบ้านด้วย ไม่ใช่แค่หน้าจอ (UAT B1 แบบ dry run เจอ 19/10/2026)"""
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from routes.forms import form_approval_routes as fa


class _Q:
    def __init__(self, row): self.row = row
    def filter(self, *a, **k): return self
    def first(self): return self.row


class _Db:
    def __init__(self, sub): self.sub = sub
    def query(self, *a, **k): return _Q(self.sub)
    def refresh(self, *a, **k): pass


class _PassedTheGuard(Exception):
    pass


@pytest.fixture
def wired(monkeypatch):
    sub = NS(form_id="ADV-2610-099", status_approve="In Progress", created_by="R", current_approval_level=1)
    monkeypatch.setattr(fa, "get_user_by_employee_id", lambda db, eid: NS(id=1, employee_id=eid))

    def stop(*a, **k):  # the first thing after the guard — reaching it means the guard let the call through
        raise _PassedTheGuard()

    monkeypatch.setattr(fa.approval_repo, "approval_decision", stop)
    return sub


@pytest.mark.parametrize("remark", ["", "   ", "\n\t"])
def test_blank_reason_is_refused_for_an_advance(wired, monkeypatch, remark):
    monkeypatch.setattr(fa, "is_advance_submission", lambda s: True)
    with pytest.raises(HTTPException) as exc:
        fa.reject_submission("ADV-2610-099", background_tasks=NS(add_task=lambda *a, **k: None), employee_id="A", remark=remark, db=_Db(wired))
    assert exc.value.status_code == 400
    assert exc.value.detail == "กรุณาระบุเหตุผลที่ไม่อนุมัติ"


def test_a_real_reason_goes_through(wired, monkeypatch):
    monkeypatch.setattr(fa, "is_advance_submission", lambda s: True)
    with pytest.raises(_PassedTheGuard):
        fa.reject_submission("ADV-2610-099", background_tasks=NS(add_task=lambda *a, **k: None), employee_id="A", remark="ยอดสูงเกินจำเป็น", db=_Db(wired))


def test_other_forms_keep_their_old_behaviour(wired, monkeypatch):
    """แบบฟอร์มอื่น (เช่นแจ้งซ่อม IT) ไม่โดนกฎนี้ หน้าจอเดิมของเขาอาจส่งเหตุผลว่างมา"""
    monkeypatch.setattr(fa, "is_advance_submission", lambda s: False)
    monkeypatch.setattr(fa, "get_user_by_employee_id", lambda db, eid: (_ for _ in ()).throw(_PassedTheGuard()))
    with pytest.raises(_PassedTheGuard):
        fa.reject_submission("IT-2610-001", background_tasks=NS(add_task=lambda *a, **k: None), employee_id="A", remark="", db=_Db(wired))
