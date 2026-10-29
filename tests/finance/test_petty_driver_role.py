"""พจส./พจร. ต่อแถว ตำแหน่งยาง และเลขที่ใบรับสภาพหนี้ (ผู้ใช้สั่ง 29/10/2026)

- ใบที่กรอกเป็นแถว: ทุกแถวเลือก พจส. หรือ พจร. · ใบเดี่ยวเลือกที่หัวใบ (detail.driver_role)
- ใบยาง: ตำแหน่งยางต่อแถว เลือกได้หลายตำแหน่ง รหัสเดียวกับ WMS (F1–F4 · RA1–RA8 · RB1–RB13)
- มี พจร. ในใบ (ทุกประเภท) → ต้องมีเลขที่ใบรับสภาพหนี้ อ้างอิงแค่เลขเอกสาร
  เดิมบังคับตอนอนุมัติ · ผู้ใช้เปลี่ยน 29/10/2026 "กรอกเลขใบรับสภาพหนี้ ทำหลังจากโอนเงินแล้วได้"
  → อนุมัติได้เลย เจ้าของเงินสดใส่เลขทีหลังแบบเลข DD และด่านบังคับย้ายไปตอนส่งเคลียร์
"""
from datetime import date

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from models.finance_petty_model import FinPettyExpenseType
from routes.finance import petty_routes as R
from models.finance_petty_model import FinPettyLog
from schemas.finance_petty_schema import ApproveIn, ClaimItemIn, DebtDocIn, RoundIn
from services.finance import petty_repo as repo
from services.finance import petty_validate as V

TIRE_SPEC = {
    "item_based": True,
    "item_fields": [
        {"key": "event_date", "label": "วันที่ตามบิล", "type": "date", "required": True},
        {"key": "driver_role", "label": "พจส./พจร.", "type": "select", "options": ["พจส.", "พจร."],
         "required": True, "default": "พจส."},
        {"key": "driver_name", "label": "ชื่อ พจส./พจร.", "type": "text", "required": False},
        {"key": "tire_position", "label": "ตำแหน่งยาง", "type": "tire_positions", "required": True},
        {"key": "qty", "label": "จำนวน (เส้น)", "type": "number", "required": True},
        {"key": "rate", "label": "ราคา/เส้น", "type": "money", "required": True},
    ],
    "attachments": [{"key": "transfer", "label": "หลักฐานโอนเงิน", "required": True}],
}
PAID = {"transfer": 1}


@pytest.fixture
def tire(db_session, fund):
    db_session.add(FinPettyExpenseType(code="TIRE", name_th="ค่าปะ-เปลี่ยนยาง", spec=TIRE_SPEC))
    db_session.commit()


def _row(**over):
    row = {"event_date": date(2026, 10, 20), "truck_code": "161", "truck_plate": "71-4285", "ref_no": "B-1",
           "qty": 2, "rate": 600, "driver_role": "พจส.", "driver_name": "สมชาย", "tire_position": "RA1,RA2"}
    row.update(over)
    return row


def _open(db_session, claim_payload, *rows, **over):
    return repo.create_claim(db_session, claim_payload(expense_type="TIRE", **over), list(rows), actor="55555")


class TestStoredPerRow:
    def test_role_and_positions_come_back(self, db_session, tire, claim_payload, seq_stub, frozen_bkk):
        claim = _open(db_session, claim_payload, _row(driver_role="พจร.", tire_position="F1,RA1,RB13"))
        item = R.get_claim(claim.form_id, db=db_session)["items"][0]
        assert item["driver_role"] == "พจร." and item["tire_position"] == "F1,RA1,RB13"

    def test_schema_takes_only_the_two_roles(self):
        assert ClaimItemIn(event_date=date(2026, 10, 20), rate=1, driver_role="พจร.").driver_role == "พจร."
        with pytest.raises(ValidationError):
            ClaimItemIn(event_date=date(2026, 10, 20), rate=1, driver_role="คนขับ")

    def test_schema_takes_only_wms_tire_positions(self):
        row = ClaimItemIn(event_date=date(2026, 10, 20), rate=1, tire_position=" ra1, RA2 ,f3 ")
        assert row.tire_position == "RA1,RA2,F3"
        with pytest.raises(ValidationError):
            ClaimItemIn(event_date=date(2026, 10, 20), rate=1, tire_position="RA1,ZZ9")

    def test_blank_role_and_position_are_none(self):
        row = ClaimItemIn(event_date=date(2026, 10, 20), rate=1, driver_role="", tire_position="")
        assert row.driver_role is None and row.tire_position is None


class TestDebtDocNeeded:
    def test_any_pjr_row_needs_it(self):
        assert V.needs_debt_doc([{"driver_role": "พจส."}, {"driver_role": "พจร."}], {}) is True

    def test_pjs_only_does_not(self):
        assert V.needs_debt_doc([{"driver_role": "พจส."}, {"driver_role": None}], {}) is False

    def test_single_claims_use_the_header(self):
        assert V.needs_debt_doc([{"driver_role": None}], {"driver_role": "พจร."}) is True


class TestApprovalNoLongerAsksForTheDebtDoc:
    """ผู้ใช้สั่ง 29/10/2026: เลขใบรับสภาพหนี้กรอกหลังโอนเงินได้ — อนุมัติไม่ต้องรอเลข"""

    def test_pjr_without_the_number_is_approved(self, db_session, tire, claim_payload, seq_stub, frozen_bkk):
        claim = _open(db_session, claim_payload, _row(driver_role="พจร."))
        out = R.approve_claim(claim.form_id, ApproveIn(employee_id="12345", attachments=PAID), db_session)
        assert out["status"] == "APPROVED" and out["debt_doc_no"] is None

    def test_the_number_is_still_kept_when_given(self, db_session, tire, claim_payload, seq_stub, frozen_bkk):
        claim = _open(db_session, claim_payload, _row(driver_role="พจร."))
        out = R.approve_claim(claim.form_id, ApproveIn(employee_id="12345", attachments=PAID,
                                                       debt_doc_no=" RD-2610-015 "), db_session)
        assert out["status"] == "APPROVED" and out["debt_doc_no"] == "RD-2610-015"

    def test_pjs_needs_no_number(self, db_session, tire, claim_payload, seq_stub, frozen_bkk):
        claim = _open(db_session, claim_payload, _row())
        out = R.approve_claim(claim.form_id, ApproveIn(employee_id="12345", attachments=PAID), db_session)
        assert out["status"] == "APPROVED" and out["debt_doc_no"] is None

    def test_header_level_pjr_on_a_single_claim(self, db_session, tire, claim_payload, seq_stub, frozen_bkk):
        claim = _open(db_session, claim_payload, _row(), detail={"driver_role": "พจร."})
        out = R.approve_claim(claim.form_id, ApproveIn(employee_id="12345", attachments=PAID), db_session)
        assert out["status"] == "APPROVED" and out["needs_debt_doc"] is True


class TestClaimSaysIfItNeedsTheDebtDoc:
    """FE ไม่ต้องคิดเองว่าใบไหนมี พจร. — BE บอกมาในใบเลย (ทั้งรายการและรายละเอียด)"""

    def test_detail_of_a_pjr_claim(self, db_session, tire, claim_payload, seq_stub, frozen_bkk):
        claim = _open(db_session, claim_payload, _row(), _row(driver_role="พจร.", ref_no="B-2"))
        assert R.get_claim(claim.form_id, db=db_session)["needs_debt_doc"] is True

    def test_detail_of_a_pjs_claim(self, db_session, tire, claim_payload, seq_stub, frozen_bkk):
        claim = _open(db_session, claim_payload, _row())
        assert R.get_claim(claim.form_id, db=db_session)["needs_debt_doc"] is False

    def test_header_level_pjr(self, db_session, tire, claim_payload, seq_stub, frozen_bkk):
        claim = _open(db_session, claim_payload, _row(driver_role=None), detail={"driver_role": "พจร."})
        assert R.get_claim(claim.form_id, db=db_session)["needs_debt_doc"] is True

    def test_list(self, db_session, tire, claim_payload, seq_stub, frozen_bkk):
        pjr = _open(db_session, claim_payload, _row(driver_role="พจร."))
        pjs = _open(db_session, claim_payload, _row(ref_no="B-2"))
        rows = {c["form_id"]: c for c in R.list_claims(limit=50, offset=0, db=db_session)["items"]}
        assert rows[pjr.form_id]["needs_debt_doc"] is True and rows[pjs.form_id]["needs_debt_doc"] is False


@pytest.fixture
def approved_pjr(db_session, tire, claim_payload, seq_stub, frozen_bkk):
    """ใบยางที่มี พจร. อนุมัติจ่ายแล้ว ยังไม่มีเลขใบรับสภาพหนี้"""
    def _make(**row):
        claim = _open(db_session, claim_payload, _row(**{"driver_role": "พจร.", **row}))
        R.approve_claim(claim.form_id, ApproveIn(employee_id="12345", attachments=PAID,
                                                 dup_override_reason="คนละเส้น"), db_session)
        return claim
    return _make


def _debt(claim, number, employee_id="12345"):
    return DebtDocIn(employee_id=employee_id, debt_doc_no=number)


class TestHolderAddsTheDebtDocAfterPaying:
    """POST /claims/{form_id}/debt-doc — เหมือน /dd: เจ้าของเงินสด · ใบที่อนุมัติจ่ายแล้ว ยังไม่ส่งเคลียร์"""

    def test_holder_adds_it_and_it_is_trimmed(self, db_session, approved_pjr):
        claim = approved_pjr()
        out = R.set_claim_debt_doc(claim.form_id, _debt(claim, " RD-2610-015 "), db_session)
        assert out["debt_doc_no"] == "RD-2610-015" and out["needs_debt_doc"] is True
        assert out["status"] == "APPROVED"

    def test_change_is_logged_old_to_new(self, db_session, approved_pjr):
        claim = approved_pjr()
        R.set_claim_debt_doc(claim.form_id, _debt(claim, "RD-1"), db_session)
        R.set_claim_debt_doc(claim.form_id, _debt(claim, "RD-2"), db_session)
        logs = (db_session.query(FinPettyLog).filter(FinPettyLog.claim_id == claim.id,
                                                     FinPettyLog.action == "SET_DEBT_DOC")
                .order_by(FinPettyLog.id).all())
        assert [row.changes["debt_doc_no"] for row in logs] == [[None, "RD-1"], ["RD-1", "RD-2"]]
        assert logs[0].action_by == "12345"
        assert logs[0].remark == "เลขใบรับสภาพหนี้: - → RD-1"
        assert logs[1].remark == "เลขใบรับสภาพหนี้: RD-1 → RD-2"

    def test_only_the_holder(self, db_session, approved_pjr):
        claim = approved_pjr()
        with pytest.raises(HTTPException) as exc:
            R.set_claim_debt_doc(claim.form_id, _debt(claim, "RD-1", employee_id="99999"), db_session)
        assert exc.value.status_code == 403

    def test_not_before_approval(self, db_session, tire, claim_payload, seq_stub, frozen_bkk):
        claim = _open(db_session, claim_payload, _row(driver_role="พจร."))
        with pytest.raises(HTTPException) as exc:
            R.set_claim_debt_doc(claim.form_id, _debt(claim, "RD-1"), db_session)
        assert exc.value.status_code == 409

    def test_not_after_the_round_is_sent(self, db_session, approved_pjr, line):
        claim = approved_pjr()
        R.set_claim_debt_doc(claim.form_id, _debt(claim, "RD-1"), db_session)
        R.create_round(_round_in(claim), db_session)
        with pytest.raises(HTTPException) as exc:
            R.set_claim_debt_doc(claim.form_id, _debt(claim, "RD-2"), db_session)
        assert exc.value.status_code == 409

    def test_claim_without_pjr_is_refused(self, db_session, tire, claim_payload, seq_stub, frozen_bkk):
        claim = _open(db_session, claim_payload, _row())
        R.approve_claim(claim.form_id, ApproveIn(employee_id="12345", attachments=PAID), db_session)
        with pytest.raises(HTTPException) as exc:
            R.set_claim_debt_doc(claim.form_id, _debt(claim, "RD-1"), db_session)
        assert exc.value.status_code == 409
        assert exc.value.detail == "ใบนี้ไม่มี พจร. ไม่ต้องใส่เลขใบรับสภาพหนี้"

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_number_is_422_in_thai(self, db_session, approved_pjr, blank):
        claim = approved_pjr()
        with pytest.raises(HTTPException) as exc:
            R.set_claim_debt_doc(claim.form_id, _debt(claim, blank), db_session)
        assert exc.value.status_code == 422
        assert exc.value.detail == "กรอกเลขใบรับสภาพหนี้ก่อน"

    def test_longer_than_50_is_422_in_thai(self, db_session, approved_pjr):
        claim = approved_pjr()
        with pytest.raises(HTTPException) as exc:
            R.set_claim_debt_doc(claim.form_id, _debt(claim, "R" * 51), db_session)
        assert exc.value.status_code == 422
        assert exc.value.detail == "เลขใบรับสภาพหนี้ยาวได้ไม่เกิน 50 ตัวอักษร"

    def test_50_after_trimming_is_fine(self, db_session, approved_pjr):
        claim = approved_pjr()
        out = R.set_claim_debt_doc(claim.form_id, _debt(claim, "  " + "R" * 50 + "  "), db_session)
        assert out["debt_doc_no"] == "R" * 50

    def test_schema_lets_blank_through_to_the_thai_message(self):
        # Pydantic min_length จะพูดภาษาอังกฤษใส่ผู้ใช้ — ปล่อยให้ route ตอบเป็นไทย (ดู test_thai_messages)
        assert DebtDocIn(employee_id="12345", debt_doc_no="").debt_doc_no == ""

    @pytest.mark.parametrize("body", [{"employee_id": "12345", "debt_doc_no": "  "}, {"employee_id": "12345"}])
    def test_blank_over_http_comes_back_as_plain_thai(self, body):
        """ยิงผ่าน TestClient จริง: ต้องได้ 422 ที่ detail เป็นข้อความไทย ไม่ใช่ลิสต์ error ของ Pydantic"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import database

        def _no_db():
            yield None  # ช่องว่างถูกตีกลับก่อนแตะ DB

        app = FastAPI()
        app.include_router(R.router)
        app.dependency_overrides[database.get_db] = _no_db
        res = TestClient(app).post("/finance/petty/claims/LB-B02-2610-013/debt-doc", json=body)
        assert res.status_code == 422 and res.json() == {"detail": "กรอกเลขใบรับสภาพหนี้ก่อน"}


HOLDER_LEVEL = 4


@pytest.fixture
def line(monkeypatch):
    """สายงานของเจ้าของเงินสด 12345 — ผู้จัดการ 600100 แผนกเดียวกัน"""
    def _person(eid, uid, level):
        return {"employee_id": eid, "id": uid, "level": level, "department_id": 30,
                "division_id": 7, "site_id": 3, "active": True}
    people = {"12345": _person("12345", 1, HOLDER_LEVEL), "600100": _person("600100", 2, 5)}
    monkeypatch.setattr(repo, "line_context", lambda db, fresh=True: (people, {}))
    return people


def _round_in(*claims):
    return RoundIn(employee_id="12345", fund_id=claims[0].fund_id, claim_ids=[c.id for c in claims])


class TestRoundAsksForTheDebtDoc:
    """ส่งเคลียร์ = ด่านที่บังคับเลขใบรับสภาพหนี้ (ย้ายมาจากด่านอนุมัติ) · ไม่ครบใบเดียว ไม่ส่งทั้งรอบ"""

    def test_missing_number_is_409_naming_the_claim(self, db_session, approved_pjr, line):
        claim = approved_pjr()
        with pytest.raises(HTTPException) as exc:
            R.create_round(_round_in(claim), db_session)
        assert exc.value.status_code == 409
        assert exc.value.detail["form_ids"] == [claim.form_id]
        assert exc.value.detail["error"] == (
            f"ใบ {claim.form_id} มีคนขับ พจร. ยังไม่ได้ใส่เลขใบรับสภาพหนี้")

    def test_with_the_number_the_round_goes(self, db_session, approved_pjr, line):
        claim = approved_pjr()
        R.set_claim_debt_doc(claim.form_id, _debt(claim, "RD-1"), db_session)
        out = R.create_round(_round_in(claim), db_session)
        assert out["status"] == "CLEAR_PENDING" and out["claims"][0]["debt_doc_no"] == "RD-1"

    def test_number_given_at_approval_counts(self, db_session, tire, claim_payload, seq_stub, frozen_bkk, line):
        claim = _open(db_session, claim_payload, _row(driver_role="พจร."))
        R.approve_claim(claim.form_id, ApproveIn(employee_id="12345", attachments=PAID,
                                                 debt_doc_no="RD-9"), db_session)
        assert R.create_round(_round_in(claim), db_session)["status"] == "CLEAR_PENDING"

    def test_pjs_claims_need_nothing(self, db_session, tire, claim_payload, seq_stub, frozen_bkk, line):
        claim = _open(db_session, claim_payload, _row())
        R.approve_claim(claim.form_id, ApproveIn(employee_id="12345", attachments=PAID), db_session)
        assert R.create_round(_round_in(claim), db_session)["status"] == "CLEAR_PENDING"

    def test_one_missing_blocks_the_whole_round(self, db_session, approved_pjr, line):
        ready = approved_pjr()
        R.set_claim_debt_doc(ready.form_id, _debt(ready, "RD-1"), db_session)
        a, b = approved_pjr(ref_no="B-2"), approved_pjr(ref_no="B-3")
        with pytest.raises(HTTPException) as exc:
            R.create_round(_round_in(ready, a, b), db_session)
        assert exc.value.status_code == 409
        assert exc.value.detail["form_ids"] == [a.form_id, b.form_id]
        assert exc.value.detail["error"] == (
            f"ใบ {a.form_id}, {b.form_id} มีคนขับ พจร. ยังไม่ได้ใส่เลขใบรับสภาพหนี้")
        db_session.expire_all()
        assert {c.status for c in (ready, a, b)} == {"APPROVED"}
        assert {c.batch_id for c in (ready, a, b)} == {None}


class TestRequiredOnEntry:
    def test_tyre_row_without_a_position_is_refused(self, db_session, tire, claim_payload, seq_stub, frozen_bkk):
        with pytest.raises(HTTPException) as exc:
            R._reject_incomplete_rows(db_session, "TIRE", [_row(tire_position=None)])
        assert any(m["key"] == "tire_position" for m in exc.value.detail["missing"])


class TestClaimCarriesItsOpenTime:
    """ใบสำคัญจ่าย: ช่องผู้ขอเบิกใช้เวลาเปิดใบเป็นตรา e-Signature (ผู้ใช้สั่ง 29/10/2026)"""

    def test_detail_has_created_at(self, db_session, tire, claim_payload, seq_stub, frozen_bkk):
        claim = _open(db_session, claim_payload, _row())
        out = R.get_claim(claim.form_id, db=db_session)
        assert isinstance(out["created_at"], str) and out["created_at"][:4].isdigit()
