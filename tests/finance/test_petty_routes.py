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


class TestProjectCodeTravelsWithTheClaim:
    def test_the_detail_response_carries_it(self, db_session, claim):
        body = R.get_claim(claim.form_id, db=db_session)
        assert body["project_code"] == "PJ-LB-B02-2610-001"


class TestCreateRejectsIncompleteRows:
    """ผู้ใช้สั่ง: เลขที่บัตรชั่งต้องกรอกเสมอ — ต้องกันตั้งแต่ตอนเปิดใบ ไม่ใช่รอถึงตอนอนุมัติ
    (เจอจากการรันจริง 2026-10-13: แถวที่ ref_no ว่างเปิดใบผ่าน)"""

    def _payload(self, fund, items):
        from datetime import date
        from schemas.finance_petty_schema import ClaimIn
        return ClaimIn(fund_id=fund.id, expense_type="WEIGH", expense_date=date(2026, 9, 30),
                       payee_name="x", created_by="55555", items=items)

    def test_a_blank_ticket_number_cannot_be_saved(self, db_session, fund, weigh_type, weigh_items,
                                                  seq_stub, frozen_bkk):
        bad = [dict(weigh_items[0], ref_no="   ")]
        with pytest.raises(HTTPException) as exc:
            R.create_claim(self._payload(fund, bad), db=db_session)
        assert exc.value.status_code == 422
        assert exc.value.detail["missing"][0]["key"] == "ref_no"
        assert exc.value.detail["missing"][0]["line_no"] == 1

    def test_a_complete_row_still_saves(self, db_session, fund, weigh_type, weigh_items,
                                        seq_stub, frozen_bkk):
        out = R.create_claim(self._payload(fund, weigh_items[:1]), db=db_session)
        assert out["status"] == "PENDING_APPROVAL"

    def test_editing_cannot_blank_the_ticket_either(self, db_session, fund, weigh_type, weigh_items,
                                                    seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, {"fund_id": fund.id, "expense_type": "WEIGH",
                                               "expense_date": __import__("datetime").date(2026, 9, 30),
                                               "payee_name": "x", "created_by": "55555"},
                                  weigh_items[:1], actor="55555")
        with pytest.raises(HTTPException) as exc:
            R.update_claim(claim.form_id, self._payload(fund, [dict(weigh_items[0], ref_no="")]),
                           db=db_session)
        assert exc.value.status_code == 422
