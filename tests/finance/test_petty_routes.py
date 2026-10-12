"""เทสต์ระดับ route โดยเรียกฟังก์ชันตรง ๆ (TestClient ต้อง import main ซึ่งต้องมี boto3)"""
import pytest
from fastapi import HTTPException

from routes.finance import petty_routes as R
from services.finance import petty_logic as L
from services.finance import petty_repo as repo
from schemas.finance_petty_schema import ApproveIn, ReviewIn, SubmitAccIn, TransferIn

WEIGH_SPEC = {
    "item_based": True,
    "item_fields": [{"key": "ref_no", "label": "เลขที่บัตรชั่ง", "required": True}],
    "attachments": [
        {"key": "weigh_slip", "label": "ใบชั่งน้ำหนัก (สำเนา)", "required": True},
        {"key": "transfer", "label": "หลักฐานโอนเงิน", "required": True},
    ],
    "rules": [{"when": "total >= 500", "require_field": "dd_no", "at": "BEFORE_SUBMIT_TO_ACCOUNTING"}],
    "duplicate_check": {
        "hard": {"keys": ["ref_no", "truck_code", "truck_plate"], "days": 365, "action": "block"},
        "soft": {"keys": ["event_date", "truck"], "days": 7, "action": "warn"},
    },
}

FULL = {"weigh_slip": 16, "transfer": 1}


@pytest.fixture
def weigh_type(db_session):
    row = repo.get_expense_type(db_session, "WEIGH")
    row.spec = WEIGH_SPEC
    db_session.commit()
    return row


@pytest.fixture
def claim(db_session, fund, weigh_type, claim_payload, weigh_items, seq_stub, frozen_bkk):
    return repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")


class TestApprovalGate:
    def test_cannot_approve_without_the_weigh_slip(self, db_session, claim):
        with pytest.raises(HTTPException) as exc:
            R.approve_claim(claim.form_id, ApproveIn(employee_id="12345", attachments={"transfer": 1}),
                            db_session)
        assert exc.value.status_code == 403
        assert any(m["key"] == "weigh_slip" for m in exc.value.detail["missing"])

    def test_the_clerk_is_told_which_row_is_missing_its_ticket(self, db_session, fund, weigh_type,
                                                               claim_payload, weigh_items,
                                                               seq_stub, frozen_bkk):
        items = [dict(weigh_items[0]), dict(weigh_items[1], ref_no="")]
        row = repo.create_claim(db_session, claim_payload(), items, actor="55555")
        with pytest.raises(HTTPException) as exc:
            R.approve_claim(row.form_id, ApproveIn(employee_id="12345", attachments=FULL), db_session)
        missing = [m for m in exc.value.detail["missing"] if m["key"] == "ref_no"]
        assert missing and missing[0]["line_no"] == 2

    def test_the_real_voucher_warns_once_for_the_1093_pair(self, db_session, claim):
        # ใบจริงมีรถ 1093 สองแถว (คนละบัตรชั่ง) → ด่านอ่อนต้องเตือน ไม่บล็อก
        with pytest.raises(HTTPException) as exc:
            R.approve_claim(claim.form_id, ApproveIn(employee_id="12345", attachments=FULL),
                            db_session)
        assert exc.value.status_code == 422
        dups = exc.value.detail["duplicates"]
        assert len(dups) == 1 and dups[0]["truck_code"] == "1093"

    def test_a_complete_claim_is_approved_once_the_warning_is_acknowledged(self, db_session, claim):
        out = R.approve_claim(claim.form_id,
                              ApproveIn(employee_id="12345", attachments=FULL,
                                        dup_override_reason="ชั่ง 2 เที่ยว"), db_session)
        assert out["status"] == "APPROVED" and out["amount"] == 1640.0

    def test_a_single_row_claim_needs_no_override(self, db_session, fund, weigh_type, claim_payload,
                                                  weigh_items, seq_stub, frozen_bkk):
        row = repo.create_claim(db_session, claim_payload(), weigh_items[:1], actor="55555")
        out = R.approve_claim(row.form_id, ApproveIn(employee_id="12345", attachments=FULL),
                              db_session)
        assert out["status"] == "APPROVED"

    def test_only_the_fund_holder_may_approve(self, db_session, claim):
        with pytest.raises(HTTPException) as exc:
            R.approve_claim(claim.form_id, ApproveIn(employee_id="99999", attachments=FULL), db_session)
        assert exc.value.status_code == 403

    def test_a_hard_duplicate_blocks_with_no_override(self, db_session, fund, weigh_type,
                                                      claim_payload, weigh_items, seq_stub,
                                                      frozen_bkk):
        first = repo.create_claim(db_session, claim_payload(), weigh_items[:1], actor="55555")
        R.approve_claim(first.form_id, ApproveIn(employee_id="12345", attachments=FULL), db_session)
        again = repo.create_claim(db_session, claim_payload(), weigh_items[:1], actor="55555")
        with pytest.raises(HTTPException) as exc:
            R.approve_claim(again.form_id,
                            ApproveIn(employee_id="12345", attachments=FULL,
                                      dup_override_reason="ชั่ง 2 เที่ยว"), db_session)
        assert exc.value.status_code == 409  # เหตุผลทับไม่ช่วยในด่านเข้ม

    def test_a_soft_duplicate_asks_for_a_reason_then_lets_it_through(self, db_session, fund,
                                                                     weigh_type, claim_payload,
                                                                     weigh_items, seq_stub,
                                                                     frozen_bkk):
        # รถคันเดียวกัน วันเดียวกัน แต่เลขบัตรต่างกัน = ของจริงเคส 1093
        first = repo.create_claim(db_session, claim_payload(), [weigh_items[0]], actor="55555")
        R.approve_claim(first.form_id, ApproveIn(employee_id="12345", attachments=FULL), db_session)
        second = repo.create_claim(db_session, claim_payload(),
                                   [dict(weigh_items[0], ref_no="000999")], actor="55555")
        with pytest.raises(HTTPException) as exc:
            R.approve_claim(second.form_id, ApproveIn(employee_id="12345", attachments=FULL),
                            db_session)
        assert exc.value.status_code == 422
        out = R.approve_claim(second.form_id,
                              ApproveIn(employee_id="12345", attachments=FULL,
                                        dup_override_reason="ชั่ง 2 เที่ยวจริง"), db_session)
        assert out["status"] == "APPROVED" and out["dup_override_reason"] == "ชั่ง 2 เที่ยวจริง"


class TestSubmitAccountingGate:
    def _to_transferred(self, db_session, claim):
        R.approve_claim(claim.form_id,
                        ApproveIn(employee_id="12345", attachments=FULL,
                                  dup_override_reason="ชั่ง 2 เที่ยว"), db_session)
        R.transfer_claim(claim.form_id,
                         TransferIn(employee_id="12345", transfer_date="2026-09-25"), db_session)

    def test_dd_is_required_over_500(self, db_session, claim):
        self._to_transferred(db_session, claim)
        with pytest.raises(HTTPException) as exc:
            R.submit_to_accounting(claim.form_id, SubmitAccIn(employee_id="12345", attachments=FULL),
                                   db_session)
        assert exc.value.status_code == 403
        assert any(m["key"] == "dd_no" for m in exc.value.detail["missing"])

    def test_dd_given_with_the_command_satisfies_the_gate(self, db_session, claim):
        self._to_transferred(db_session, claim)
        out = R.submit_to_accounting(claim.form_id,
                                     SubmitAccIn(employee_id="12345", dd_no="LBDD26090931",
                                                 attachments=FULL), db_session)
        assert out["status"] == "AWAITING_REVIEW" and out["dd_no"] == "LBDD26090931"


class TestDuplicateCheckEndpoint:
    def test_route_is_matched_before_the_form_id_route(self):
        # /claims/duplicate-check ต้องไม่ถูก /claims/{form_id} จับไปก่อน
        paths = [r.path for r in R.router.routes]
        assert paths.index("/finance/petty/claims/duplicate-check") < \
               paths.index("/finance/petty/claims/{form_id}")

    def test_reports_a_hard_duplicate_for_one_row(self, db_session, fund, weigh_type, claim_payload,
                                                  weigh_items, seq_stub, frozen_bkk):
        repo.create_claim(db_session, claim_payload(), weigh_items[:1], actor="55555")
        out = R.duplicate_check(expense_type="WEIGH", event_date="2026-09-17", truck_code="161",
                                truck_plate="71-4285", ref_no="000055", db=db_session)
        assert len(out["hard"]) == 1

    def test_a_clean_row_reports_nothing(self, db_session, fund, weigh_type, claim_payload,
                                         weigh_items, seq_stub, frozen_bkk):
        repo.create_claim(db_session, claim_payload(), weigh_items[:1], actor="55555")
        out = R.duplicate_check(expense_type="WEIGH", event_date="2026-09-17", truck_code="777",
                                truck_plate="71-0000", ref_no="999999", db=db_session)
        assert out["hard"] == [] and out["soft"] == []

    def test_a_bad_date_is_rejected(self, db_session):
        with pytest.raises(HTTPException) as exc:
            R.duplicate_check(expense_type="WEIGH", event_date="17/09/2026", db=db_session)
        assert exc.value.status_code == 400


class TestReviewGate:
    def test_accounting_cannot_pass_a_claim_that_is_not_awaiting_review(self, db_session, claim):
        with pytest.raises(HTTPException) as exc:
            R.review_claim(claim.form_id, ReviewIn(employee_id="80001", action="PASS"), db_session)
        assert exc.value.status_code == 409

    def test_send_back_needs_a_reason(self, db_session, claim):
        with pytest.raises(HTTPException) as exc:
            R.review_claim(claim.form_id, ReviewIn(employee_id="80001", action="SEND_BACK"),
                           db_session)
        assert exc.value.status_code == 422


class TestTheNumberIsTheOnlyNumber:
    def test_the_detail_response_has_no_project_code(self, db_session, claim):
        body = R.get_claim(claim.form_id, db=db_session)
        assert "project_code" not in body and body["form_id"] == "LB-B02-2610-001"


class TestWhatTheScreenNeedsComesBack:
    """เจอ 2026-10-13: ช่องหัวใบ (เส้นทาง เหตุผล) เก็บใน detail แต่ไม่เคยส่งกลับไปให้หน้าจอ/ใบพิมพ์"""

    def test_detail_is_returned(self, db_session, fund, weigh_type, weigh_items, seq_stub, frozen_bkk):
        from datetime import date
        claim = repo.create_claim(db_session, {"fund_id": fund.id, "expense_type": "WEIGH",
                                               "expense_date": date(2026, 9, 30), "payee_name": "x",
                                               "created_by": "55555",
                                               "detail": {"route_from": "คลองตัน", "route_to": "เมกาบางนา"}},
                                  weigh_items[:1], actor="55555")
        body = R.get_claim(claim.form_id, db=db_session)
        assert body["detail"] == {"route_from": "คลองตัน", "route_to": "เมกาบางนา"}

    def test_driver_per_row_is_stored_and_returned(self, db_session, fund, weigh_type, weigh_items,
                                                   seq_stub, frozen_bkk):
        # ผู้ใช้สั่ง 2026-10-13: ใบสรุปรายการค่าชั่งต้องมี พจส. ต่อแถว
        from datetime import date
        rows = [dict(weigh_items[0], driver_name="สมชาย ใจดี")]
        claim = repo.create_claim(db_session, {"fund_id": fund.id, "expense_type": "WEIGH",
                                               "expense_date": date(2026, 9, 30), "payee_name": "x",
                                               "created_by": "55555"}, rows, actor="55555")
        body = R.get_claim(claim.form_id, db=db_session)
        assert body["items"][0]["driver_name"] == "สมชาย ใจดี"

    def test_driver_can_come_through_the_schema(self):
        from datetime import date
        from schemas.finance_petty_schema import ClaimItemIn
        row = ClaimItemIn(event_date=date(2026, 9, 17), rate=100, driver_name="วิชัย")
        assert row.driver_name == "วิชัย"


def _user(db, employee_id, first, last):
    from models.user_model import User
    row = User(username=f"u{employee_id}", password_hash="x", email=f"{employee_id}@test.local",
               employee_id=employee_id, firstname=first, lastname=last)
    db.add(row); db.commit()
    return row


class TestNamesTravelWithTheData:
    """ผู้ใช้สั่ง 2026-10-13 (ทำให้ครบ): ใบพิมพ์/หน้าจอต้องขึ้นชื่อพนักงาน ไม่ใช่รหัส"""

    def test_employee_names_is_one_lookup_for_many_ids(self, db_session):
        _user(db_session, "680043", "กฤตบุญ", "ลายเงิน")
        _user(db_session, "670108", "ณรงค์กรณ์", "อัจฉริยญาติ")
        names = repo.employee_names(db_session, ["680043", "670108", "999999", None])
        assert names == {"680043": "กฤตบุญ ลายเงิน", "670108": "ณรงค์กรณ์ อัจฉริยญาติ"}

    def test_claim_carries_clerk_and_approver_names(self, db_session, fund, weigh_type, weigh_items,
                                                     seq_stub, frozen_bkk):
        _user(db_session, "55555", "สายใจ", "ทองดี")
        _user(db_session, "12345", "ณรงค์กรณ์", "อัจฉริยญาติ")
        claim = repo.create_claim(db_session, claim_payload_for(fund), weigh_items[:1], actor="55555")
        body = R.get_claim(claim.form_id, db=db_session)
        assert body["created_by_name"] == "สายใจ ทองดี" and body["approved_by_name"] is None
        out = R.approve_claim(claim.form_id, ApproveIn(employee_id="12345", attachments=FULL), db=db_session)
        assert out["approved_by_name"] == "ณรงค์กรณ์ อัจฉริยญาติ"

    def test_list_carries_names_too(self, db_session, fund, weigh_type, weigh_items, seq_stub, frozen_bkk):
        _user(db_session, "55555", "สายใจ", "ทองดี")
        repo.create_claim(db_session, claim_payload_for(fund), weigh_items[:1], actor="55555")
        body = R.list_claims(employee_id="55555", holder=None, status=None, month=None, limit=50, offset=0, db=db_session)
        assert body["items"][0]["created_by_name"] == "สายใจ ทองดี"

    def test_fund_carries_holder_name(self, db_session, fund):
        _user(db_session, "12345", "ณรงค์กรณ์", "อัจฉริยญาติ")
        body = R.list_funds(holder=None, active=True, db=db_session)
        assert body[0]["holder_name"] == "ณรงค์กรณ์ อัจฉริยญาติ"

    def test_unknown_employee_gives_none_not_crash(self, db_session, fund, weigh_type, weigh_items,
                                                   seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, claim_payload_for(fund), weigh_items[:1], actor="55555")
        assert R.get_claim(claim.form_id, db=db_session)["created_by_name"] is None


def claim_payload_for(fund):
    from datetime import date
    return {"fund_id": fund.id, "expense_type": "WEIGH", "expense_date": date(2026, 9, 30),
            "payee_name": "อัญชัญ น่วมมี", "created_by": "55555"}


class TestDuplicateHintsSayWhoAndHowMuch:
    """REQUIREMENT §4.1: หน้าจอต้องโชว์ว่าซ้ำกับใบไหน — เลขที่ · วันที่ · ผู้เบิก · จำนวนเงิน"""

    def test_soft_duplicate_carries_payee_and_amount_of_the_other_claim(self, db_session, fund, weigh_type,
                                                                       weigh_items, seq_stub, frozen_bkk):
        first = repo.create_claim(db_session, dict(claim_payload_for(fund), payee_name="อัญชัญ น่วมมี"),
                                  [weigh_items[0]], actor="55555")
        repo.set_status(db_session, first, L.APPROVED, "12345", "APPROVE")
        again = repo.create_claim(db_session, claim_payload_for(fund),
                                  [dict(weigh_items[0], ref_no="OTHER")], actor="55555")
        with pytest.raises(HTTPException) as exc:
            R.approve_claim(again.form_id, ApproveIn(employee_id="12345", attachments=FULL), db=db_session)
        dup = exc.value.detail["duplicates"][0]
        assert dup["form_id"] == first.form_id
        assert dup["payee_name"] == "อัญชัญ น่วมมี" and float(dup["amount"]) == 100.0


class TestWeighTicketSequenceInTheMonth:
    """REQUIREMENT §3.1: ใบชั่งต้องระบุว่าเป็นใบที่เท่าไรของรถคันนั้นในรอบเดือน"""

    def test_each_row_knows_its_order_within_the_month(self, db_session, fund, weigh_type, weigh_items,
                                                       seq_stub, frozen_bkk):
        from datetime import date
        earlier = repo.create_claim(db_session, claim_payload_for(fund),
                                    [dict(weigh_items[0], event_date=date(2026, 9, 3), ref_no="A1")], actor="55555")
        repo.set_status(db_session, earlier, L.APPROVED, "12345", "APPROVE")
        now = repo.create_claim(db_session, claim_payload_for(fund),
                                [dict(weigh_items[0], event_date=date(2026, 9, 17), ref_no="A2"),
                                 dict(weigh_items[0], event_date=date(2026, 9, 20), ref_no="A3"),
                                 dict(weigh_items[1], event_date=date(2026, 9, 20), ref_no="B1")], actor="55555")
        body = R.get_claim(now.form_id, db=db_session)
        assert [i["month_seq"] for i in body["items"]] == [2, 3, 1]

    def test_returned_claims_do_not_count(self, db_session, fund, weigh_type, weigh_items, seq_stub, frozen_bkk):
        from datetime import date
        gone = repo.create_claim(db_session, claim_payload_for(fund),
                                 [dict(weigh_items[0], event_date=date(2026, 9, 3), ref_no="A1")], actor="55555")
        repo.set_status(db_session, gone, L.RETURNED, "12345", "RETURN")
        now = repo.create_claim(db_session, claim_payload_for(fund),
                                [dict(weigh_items[0], event_date=date(2026, 9, 17), ref_no="A2")], actor="55555")
        assert R.get_claim(now.form_id, db=db_session)["items"][0]["month_seq"] == 1
