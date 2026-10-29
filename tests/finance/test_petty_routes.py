"""เทสต์ระดับ route โดยเรียกฟังก์ชันตรง ๆ (TestClient ต้อง import main ซึ่งต้องมี boto3)"""
import pytest
from fastapi import HTTPException

from routes.finance import petty_routes as R
from services.finance import petty_logic as L
from services.finance import petty_repo as repo
from schemas.finance_petty_schema import (
    ApproveIn, DdIn, PayIn, ReviewIn, RoundActionIn, RoundIn, RoundRejectIn, VoucherIn,
)

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


HOLDER_LEVEL = 4


def _person(eid, uid, level, dept=30, division=7, site=3, active=True):
    return {"employee_id": eid, "id": uid, "level": level, "department_id": dept,
            "division_id": division, "site_id": site, "active": active}


@pytest.fixture
def line(monkeypatch):
    """สายงานของเจ้าของเงินสด 12345 (ระดับ 4): ผู้จัดการ 600100 แผนกเดียวกัน · 600200 อยู่แผนกอื่น"""
    people = {p["employee_id"]: p for p in (
        _person("12345", 1, HOLDER_LEVEL), _person("600100", 2, 5), _person("600200", 3, 6, dept=99, division=99),
    )}
    monkeypatch.setattr(repo, "line_context", lambda db, fresh=True: (people, {}))
    return people


def _approve(db_session, claim):
    return R.approve_claim(claim.form_id,
                           ApproveIn(employee_id="12345", attachments=FULL, transfer_date="2026-09-25",
                                     dup_override_reason="ชั่ง 2 เที่ยว"), db_session)


class TestApproveIsAlsoThePayment:
    def test_approval_records_the_payment_date(self, db_session, claim):
        out = _approve(db_session, claim)
        assert out["status"] == "APPROVED" and out["transfer_date"] == "2026-09-25"

    def test_payment_date_defaults_to_today(self, db_session, claim):
        out = R.approve_claim(claim.form_id, ApproveIn(employee_id="12345", attachments=FULL,
                                                       dup_override_reason="x"), db_session)
        assert out["transfer_date"] == str(L.bkk_today())


class TestDdBeforeTheRound:
    def test_holder_sets_the_dd(self, db_session, claim):
        _approve(db_session, claim)
        out = R.set_claim_dd(claim.form_id, DdIn(employee_id="12345", dd_no="LBDD26090931"), db_session)
        assert out["dd_no"] == "LBDD26090931"

    def test_only_the_holder(self, db_session, claim):
        _approve(db_session, claim)
        with pytest.raises(HTTPException) as exc:
            R.set_claim_dd(claim.form_id, DdIn(employee_id="99999", dd_no="LBDD1"), db_session)
        assert exc.value.status_code == 403


def _round_in(claim, **over):
    body = {"employee_id": "12345", "fund_id": claim.fund_id, "claim_ids": [claim.id],
            "attachments": {claim.form_id: FULL}}
    body.update(over)
    return RoundIn(**body)


class TestCreateRound:
    def test_dd_is_required_over_500_and_the_error_names_the_claim(self, db_session, claim, line):
        _approve(db_session, claim)
        with pytest.raises(HTTPException) as exc:
            R.create_round(_round_in(claim), db_session)
        assert exc.value.status_code == 403
        bad = exc.value.detail["claims"]
        assert bad[0]["form_id"] == claim.form_id and any(m["key"] == "dd_no" for m in bad[0]["missing"])

    def test_round_goes_to_the_head(self, db_session, claim, line):
        _approve(db_session, claim)
        R.set_claim_dd(claim.form_id, DdIn(employee_id="12345", dd_no="LBDD26090931"), db_session)
        out = R.create_round(_round_in(claim), db_session)
        assert out["status"] == "CLEAR_PENDING" and out["clear_required_level"] == 5
        assert out["claim_count"] == 1 and out["total_amount"] == 1640.0
        assert out["claims"][0]["status"] == "CLEAR_PENDING"

    def test_only_the_holder_may_send_a_round(self, db_session, claim, line):
        _approve(db_session, claim)
        with pytest.raises(HTTPException) as exc:
            R.create_round(_round_in(claim, employee_id="99999"), db_session)
        assert exc.value.status_code == 403

    def test_claim_not_approved_is_409(self, db_session, claim, line):
        claim.dd_no = "LBDD1"
        db_session.commit()
        with pytest.raises(HTTPException) as exc:
            R.create_round(_round_in(claim), db_session)
        assert exc.value.status_code == 409


@pytest.fixture
def sent_round(db_session, claim, line):
    _approve(db_session, claim)
    R.set_claim_dd(claim.form_id, DdIn(employee_id="12345", dd_no="LBDD26090931"), db_session)
    return R.create_round(_round_in(claim), db_session)


class TestHeadClearing:
    def test_manager_in_the_line_approves(self, db_session, sent_round):
        out = R.clear_approve_round(sent_round["id"], RoundActionIn(employee_id="600100"), db_session)
        assert out["status"] == "AWAITING_REVIEW" and out["clear_approved_by"] == "600100"
        assert out["claims"][0]["status"] == "AWAITING_REVIEW"

    def test_someone_outside_the_line_is_403(self, db_session, sent_round):
        with pytest.raises(HTTPException) as exc:
            R.clear_approve_round(sent_round["id"], RoundActionIn(employee_id="600200"), db_session)
        assert exc.value.status_code == 403

    def test_the_holder_cannot_approve_their_own_round(self, db_session, sent_round):
        with pytest.raises(HTTPException) as exc:
            R.clear_approve_round(sent_round["id"], RoundActionIn(employee_id="12345"), db_session)
        assert exc.value.status_code == 403

    def test_reject_sends_the_claims_back_to_the_holder(self, db_session, sent_round):
        out = R.clear_reject_round(sent_round["id"], RoundRejectIn(employee_id="600100", reason="บิลไม่ครบ"),
                                   db_session)
        assert out["status"] == "CLEAR_REJECTED" and out["clear_reject_reason"] == "บิลไม่ครบ"
        assert out["claims"] == []  # ใบหลุดจากรอบแล้ว

    def test_round_detail_says_who_may_clear(self, db_session, sent_round):
        assert R.get_round(sent_round["id"], employee_id="600100", db=db_session)["can_clear"] is True
        assert R.get_round(sent_round["id"], employee_id="600200", db=db_session)["can_clear"] is False

    def test_head_queue_lists_only_rounds_they_may_clear(self, db_session, sent_round):
        mine = R.list_rounds(fund_id=None, status=None, holder=None, clear_approver="600100",
                             limit=50, offset=0, db=db_session)
        theirs = R.list_rounds(fund_id=None, status=None, holder=None, clear_approver="600200",
                               limit=50, offset=0, db=db_session)
        assert [r["id"] for r in mine] == [sent_round["id"]] and theirs == []

    def test_unknown_round_is_404(self, db_session, line):
        with pytest.raises(HTTPException) as exc:
            R.clear_approve_round(9999, RoundActionIn(employee_id="600100"), db_session)
        assert exc.value.status_code == 404


class TestAccountingToPayment:
    def test_pass_voucher_pay(self, db_session, claim, sent_round):
        R.clear_approve_round(sent_round["id"], RoundActionIn(employee_id="600100"), db_session)
        out = R.review_claim(claim.form_id, ReviewIn(employee_id="80001", action="PASS"), db_session)
        assert out["status"] == "REVIEWED"
        assert R.get_round(sent_round["id"], employee_id=None, db=db_session)["status"] == "REVIEWED"
        out = R.voucher_round(sent_round["id"], VoucherIn(employee_id="80001", voucher_no="PV-2610-001",
                                                          voucher_date="2026-10-29"), db_session)
        assert out["status"] == "VOUCHERED" and out["voucher_no"] == "PV-2610-001"
        assert out["voucher_date"] == "2026-10-29" and out["pay_doc_no"] is None
        out = R.pay_round(sent_round["id"], PayIn(employee_id="90001", pay_date="2026-10-30",
                                                  pay_doc_no="PAY-6910-001"), db_session)
        assert out["status"] == "CLOSED" and out["pay_date"] == "2026-10-30"
        assert out["pay_doc_no"] == "PAY-6910-001"
        assert out["claims"][0]["status"] == "CLOSED"

    def test_new_fields_are_empty_until_filled(self, db_session, sent_round):
        assert sent_round["voucher_date"] is None and sent_round["pay_doc_no"] is None

    def test_a_blank_voucher_number_is_stored_as_empty(self, db_session, claim, sent_round):
        R.clear_approve_round(sent_round["id"], RoundActionIn(employee_id="600100"), db_session)
        R.review_claim(claim.form_id, ReviewIn(employee_id="80001", action="PASS"), db_session)
        out = R.voucher_round(sent_round["id"], VoucherIn(employee_id="80001", voucher_no="  ",
                                                          voucher_date="2026-10-29"), db_session)
        assert out["status"] == "VOUCHERED" and out["voucher_no"] is None

    def test_numbers_are_trimmed(self, db_session, claim, sent_round):
        R.clear_approve_round(sent_round["id"], RoundActionIn(employee_id="600100"), db_session)
        R.review_claim(claim.form_id, ReviewIn(employee_id="80001", action="PASS"), db_session)
        R.voucher_round(sent_round["id"], VoucherIn(employee_id="80001", voucher_no=" PV-1 ",
                                                    voucher_date="2026-10-29"), db_session)
        out = R.pay_round(sent_round["id"], PayIn(employee_id="90001", pay_date="2026-10-30", pay_doc_no="   "),
                          db_session)
        assert out["voucher_no"] == "PV-1" and out["pay_doc_no"] is None

    def test_voucher_date_is_required_and_number_is_not(self):
        from pydantic import ValidationError
        assert VoucherIn(employee_id="80001", voucher_date="2026-10-29").voucher_no is None
        with pytest.raises(ValidationError):
            VoucherIn(employee_id="80001", voucher_no="PV-1")

    def test_pay_doc_no_is_optional_and_capped_at_50(self):
        from pydantic import ValidationError
        assert PayIn(employee_id="90001", pay_date="2026-10-30").pay_doc_no is None
        with pytest.raises(ValidationError):
            PayIn(employee_id="90001", pay_date="2026-10-30", pay_doc_no="X" * 51)

    def test_cannot_pay_before_voucher(self, db_session, claim, sent_round):
        R.clear_approve_round(sent_round["id"], RoundActionIn(employee_id="600100"), db_session)
        R.review_claim(claim.form_id, ReviewIn(employee_id="80001", action="PASS"), db_session)
        with pytest.raises(HTTPException) as exc:
            R.pay_round(sent_round["id"], PayIn(employee_id="90001", pay_date="2026-10-30"), db_session)
        assert exc.value.status_code == 409


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


class TestCreateClaimRejections:
    def test_an_unknown_expense_type_is_404_not_500(self, db_session, fund, claim_payload, weigh_items):
        from schemas.finance_petty_schema import ClaimIn
        body = dict(claim_payload(), expense_type="NOPE", items=weigh_items, created_by="55555")
        with pytest.raises(HTTPException) as exc:
            R.create_claim(ClaimIn(**body), db_session)
        assert exc.value.status_code == 404
        assert "ประเภท" in exc.value.detail

    def test_a_future_dated_row_is_422_and_names_the_row(self, db_session, fund, weigh_type, claim_payload,
                                                           weigh_items):
        from schemas.finance_petty_schema import ClaimIn
        items = [dict(weigh_items[0], event_date="2099-01-01")] + weigh_items[1:]
        body = dict(claim_payload(), items=items, created_by="55555")
        with pytest.raises(HTTPException) as exc:
            R.create_claim(ClaimIn(**body), db_session)
        assert exc.value.status_code == 422
        assert exc.value.detail["missing"][0]["key"] == "event_date"
        assert exc.value.detail["missing"][0]["line_no"] == 1


class TestResubmitKeepsWhatTheScreenCannotSee:
    """ปุ่ม "ส่งอีกครั้ง" ส่งใบเดิมกลับทั้งก้อน — ค่าที่ไม่ได้ส่งกลับไปให้หน้าจอจะหายตอน PUT"""

    def test_wht_flag_comes_back_with_the_claim(self, db_session, fund, weigh_type, claim_payload, weigh_items,
                                                 seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, claim_payload(wht_applicable=True), weigh_items[:1], actor="55555")
        assert R.get_claim(claim.form_id, db=db_session)["wht_applicable"] is True
