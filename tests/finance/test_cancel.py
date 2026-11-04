"""ตีกลับแบบยกเลิก (ผู้ใช้สั่ง 04/11/2026): บัญชียกเลิกใบได้จาก รอตั้งเบิกทำจ่าย / รอจ่าย / ตีกลับให้ผู้เบิกแก้ไข
ใบไปสถานะ CANCELLED (ยกเลิก) ผู้เบิกแก้/ส่งใหม่ไม่ได้อีก มีเมลแจ้งผู้เบิกพร้อมเหตุผล (DB-free)."""
import os
from datetime import date
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

import pytest  # noqa: E402
from fastapi import BackgroundTasks, HTTPException  # noqa: E402

from models.finance_model import FinAdvance, FinAdvanceLog  # noqa: E402
from models.master_model import FormApprovalLog  # noqa: E402
from routes.finance import advance_routes as ar  # noqa: E402
from schemas.finance_schema import CancelIn  # noqa: E402
from services.finance import advance_logic as L  # noqa: E402
from services.finance import approval_logic as rules  # noqa: E402
from services.finance import finance_mail as fm  # noqa: E402
from services.finance import permission_logic as perms  # noqa: E402

from tests.finance.test_return_resubmit import SUPPLIER_SNAPSHOT, _Db, _adv, wired  # noqa: E402,F401

MIGRATION = Path(__file__).resolve().parents[2] / "scripts/migrations/2026-11-04_finance_advance_cancel.sql"


# ---------------------------- status model ----------------------------

def test_cancelled_is_a_terminal_status_with_a_thai_label():
    assert L.CANCELLED == "CANCELLED" and L.FIN_CANCELLED == "CANCELLED"
    assert L.STATUS_LABELS[L.CANCELLED] == "ยกเลิก"
    assert L.CANCELLED in L.ALL_STATUSES
    assert L.derive_status("Approved", L.FIN_CANCELLED, None, date(2026, 11, 4)) == (L.CANCELLED, False)
    assert L.derive_status("Approved", L.FIN_CANCELLED, date(2026, 1, 1), date(2026, 11, 4)) == (L.CANCELLED, False)
    assert L.status_condition_spec(L.CANCELLED) == {"approve": ["Approved"], "fin": [L.FIN_CANCELLED], "fin_null": False}


def test_cancelled_amount_is_never_outstanding():
    rows = [("Approved", "CANCELLED", 2, 0, 500, 0), ("Approved", "PAID", 1, 0, 700, 0)]
    out = L.fold_summary(rows)
    assert out["counts"]["CANCELLED"] == 2 and out["outstanding_amount"] == 700.0
    assert out["outstanding_by_status"]["CANCELLED"] == 0.0


# ---------------------------- check_cancel ----------------------------

@pytest.mark.parametrize("status", [L.AWAITING_VOUCHER, L.AWAITING_PAYMENT, L.RETURNED])
def test_cancel_allowed_before_payment_and_while_returned(status):
    L.check_cancel(status, remark="ผู้เบิกไม่ต้องใช้แล้ว")


@pytest.mark.parametrize("status", [s for s in L.ALL_STATUSES if s not in (L.AWAITING_VOUCHER, L.AWAITING_PAYMENT, L.RETURNED)])
def test_cancel_blocked_elsewhere(status):
    with pytest.raises(L.InvalidTransition) as exc:
        L.check_cancel(status, remark="x")
    assert "ตีกลับแบบยกเลิก" in str(exc.value) and L.STATUS_LABELS[status] in str(exc.value)


@pytest.mark.parametrize("remark", [None, "", "   "])
def test_cancel_needs_a_reason(remark):
    with pytest.raises(L.AdvanceRuleError) as exc:
        L.check_cancel(L.AWAITING_VOUCHER, remark=remark)
    assert str(exc.value) == "กรุณาระบุเหตุผลที่ยกเลิก"


def test_cancelled_request_is_locked_for_the_requester():
    with pytest.raises(L.InvalidTransition):
        L.check_requester_edit(L.CANCELLED, is_owner=True, step1_approved=False)
    with pytest.raises(L.InvalidTransition):
        L.check_resubmit(L.CANCELLED, is_owner=True)


# ---------------------------- route ----------------------------

def test_cancel_route_registered():
    routes = {(r.path, tuple(sorted(r.methods))) for r in ar.router.routes}
    assert ("/finance/advances/{form_id}/cancel", ("PUT",)) in routes


def test_cancel_without_fin_row_creates_cancelled_row(wired, monkeypatch):
    queued = []
    monkeypatch.setattr(ar.mail, "queue_event", lambda bg, db, event, sub, **kw: queued.append((event, sub.form_id, kw)))
    db = _Db(adv=None)
    out = ar.cancel_advance("ADV-2610-001", CancelIn(action_by="F1", remark=" ไม่ใช้แล้ว "), BackgroundTasks(), db)
    assert out == {"form_id": "ADV-2610-001", "detail": True} and db.committed
    (adv,) = db.of(FinAdvance)
    assert (adv.submission_id, adv.form_id, adv.fin_status) == (5, "ADV-2610-001", "CANCELLED")
    assert adv.voucher_no is None and adv.amount_paid is None
    (log,) = db.of(FinAdvanceLog)
    assert (log.advance_id, log.action, log.remark, log.action_by) == (42, "CANCEL", "ไม่ใช้แล้ว", "F1")
    assert log.changes == {"fin_status": [None, "CANCELLED"], "snapshot": SUPPLIER_SNAPSHOT}
    (marker,) = db.of(FormApprovalLog)
    assert (marker.submission_id, marker.level_no, marker.action, marker.action_by, marker.remark) == \
        (5, 0, rules.MARKER_CANCELLED, 77, "ไม่ใช้แล้ว")
    assert queued == [(fm.CANCELLED, "ADV-2610-001", {"remark": "ไม่ใช้แล้ว"})]


def test_cancel_from_returned_reuses_the_fin_row(wired, monkeypatch):
    monkeypatch.setattr(ar.mail, "queue_event", lambda *a, **kw: None)
    adv = _adv("RETURNED")
    db = _Db(adv=adv)
    ar.cancel_advance("ADV-2610-001", CancelIn(action_by="F1", remark="ยกเลิกตามผู้เบิกแจ้ง"), BackgroundTasks(), db)
    assert adv.fin_status == "CANCELLED" and db.of(FinAdvance) == []
    (log,) = db.of(FinAdvanceLog)
    assert log.changes == {"fin_status": ["RETURNED", "CANCELLED"], "snapshot": SUPPLIER_SNAPSHOT}


def test_cancel_at_awaiting_payment_clears_voucher(wired, monkeypatch):
    monkeypatch.setattr(ar.mail, "queue_event", lambda *a, **kw: None)
    adv = _adv("VOUCHERED", voucher_no="SADV-1", voucher_date=date(2026, 10, 1))
    db = _Db(adv=adv)
    ar.cancel_advance("ADV-2610-001", CancelIn(action_by="F1", remark="ซ้ำ"), BackgroundTasks(), db)
    assert (adv.fin_status, adv.voucher_no, adv.voucher_date) == ("CANCELLED", None, None)
    (log,) = db.of(FinAdvanceLog)
    assert log.changes == {"fin_status": ["VOUCHERED", "CANCELLED"], "voucher_no": ["SADV-1", None],
                           "voucher_date": ["2026-10-01", None], "snapshot": SUPPLIER_SNAPSHOT}


def test_cancel_after_payment_is_409(wired, monkeypatch):
    monkeypatch.setattr(ar.mail, "queue_event", lambda *a, **kw: None)
    db = _Db(adv=_adv("PAID"))
    with pytest.raises(HTTPException) as exc:
        ar.cancel_advance("ADV-2610-001", CancelIn(action_by="F1", remark="x"), BackgroundTasks(), db)
    assert exc.value.status_code == 409 and db.of(FinAdvanceLog) == [] and not db.committed


def test_cancel_without_remark_is_400(wired):
    db = _Db(adv=None)
    with pytest.raises(HTTPException) as exc:
        ar.cancel_advance("ADV-2610-001", CancelIn(action_by="F1"), BackgroundTasks(), db)
    assert exc.value.status_code == 400 and exc.value.detail == "กรุณาระบุเหตุผลที่ยกเลิก"


def test_cancel_uses_the_return_permission(wired, monkeypatch):
    seen = []
    monkeypatch.setattr(ar, "require_permission", lambda db, eid, perm: seen.append((eid, perm)) or ar.require_finance(db, eid))
    monkeypatch.setattr(ar.mail, "queue_event", lambda *a, **kw: None)
    ar.cancel_advance("ADV-2610-001", CancelIn(action_by="F1", remark="x"), BackgroundTasks(), _Db(adv=None))
    assert seen == [("F1", perms.PERM_RETURN)]


# ---------------------------- mail ----------------------------

def test_cancelled_mail_goes_to_the_requester_with_the_reason():
    assert fm.CANCELLED in fm.REQUESTER_EVENTS
    subject, html = fm.render_event_email(fm.CANCELLED, {"form_id": "ADV-2611-013", "requester_name": "ก ข",
                                                        "amount": 1500, "remark": "ผู้เบิกไม่ต้องใช้แล้ว"})
    assert "ADV-2611-013" in subject and "ยกเลิก" in subject
    assert "ผู้เบิกไม่ต้องใช้แล้ว" in html and "ยกเลิก" in html


# ---------------------------- migration ----------------------------

def test_migration_widens_the_fin_status_check_to_cancelled():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "ck_fin_advances_fin_status" in sql and "'CANCELLED'" in sql
    for code in ("VOUCHERED", "VOUCHER_REJECTED", "RETURNED", "RESUBMITTED", "PAID", "CLEAR_PENDING",
                 "CLEARING_SUBMITTED", "SENT_BACK", "CLOSED"):
        assert f"'{code}'" in sql, code
