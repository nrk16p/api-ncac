"""เทสต์ระดับ route โดยเรียกฟังก์ชันตรง ๆ (TestClient ของ main ต้องมี boto3)
เรื่องลำดับ path ใช้ TestClient กับแอปเล็กที่มีแค่ router ของ petty — ดู TestRoundRoutesOverHttp"""
import pytest
from fastapi import HTTPException

from routes.finance import petty_routes as R
from services.finance import petty_logic as L
from services.finance import petty_repo as repo
from schemas.finance_petty_schema import (
    ApproveIn, DdIn, DocNumbersIn, PayIn, ReviewIn, RoundActionIn, RoundIn, RoundRejectIn, VoucherIn,
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

    def test_head_queue_marks_who_the_round_was_sent_to(self, db_session, sent_round, line):
        # รอฉันอนุมัติ = คนที่ได้อีเมล (ชั้นต่ำสุดที่กดได้) · อนุมัติแทนได้ = กดได้แต่ไม่ได้อีเมล
        line["590100"] = _person("590100", 4, 7)

        def queue(emp):
            return R.list_rounds(fund_id=None, status=None, holder=None, clear_approver=emp,
                                 limit=50, offset=0, db=db_session)

        assert [(r["id"], r["is_primary"]) for r in queue("600100")] == [(sent_round["id"], True)]
        assert [(r["id"], r["is_primary"]) for r in queue("590100")] == [(sent_round["id"], False)]

    def test_plain_round_list_has_no_primary_flag(self, db_session, sent_round):
        rows = R.list_rounds(fund_id=None, status=None, holder=None, clear_approver=None,
                             limit=50, offset=0, db=db_session)
        assert rows and "is_primary" not in rows[0]

    def test_round_detail_marks_primary_for_the_viewer(self, db_session, sent_round, line):
        line["590100"] = _person("590100", 4, 7)
        assert R.get_round(sent_round["id"], employee_id="600100", db=db_session)["is_primary"] is True
        assert R.get_round(sent_round["id"], employee_id="590100", db=db_session)["is_primary"] is False
        assert R.get_round(sent_round["id"], employee_id="600200", db=db_session)["is_primary"] is False
        assert "is_primary" not in R.get_round(sent_round["id"], employee_id=None, db=db_session)

    def test_nobody_is_primary_once_the_round_has_moved_on(self, db_session, sent_round):
        R.clear_approve_round(sent_round["id"], RoundActionIn(employee_id="600100"), db_session)
        assert R.get_round(sent_round["id"], employee_id="600100", db=db_session)["is_primary"] is False

    def test_unknown_round_is_404(self, db_session, line):
        with pytest.raises(HTTPException) as exc:
            R.clear_approve_round(9999, RoundActionIn(employee_id="600100"), db_session)
        assert exc.value.status_code == 404


class TestRoundListPagingAndCost:
    """รายการรอบ: คิวหัวหน้ากรองสิทธิ์ก่อนตัดหน้า (ไม่งั้นหน้าแรกว่างทั้งที่มีรอบรออยู่)
    และจำนวน query ไม่โตตามจำนวนแถว (เดิมดึงกองทุน ชื่อ และใบทั้งรอบทีละแถว)"""

    @pytest.fixture
    def two_funds(self, db_session, fund, line):
        """กองทุน 12345 (600100 อนุมัติเคลียร์ได้) กับกองทุน 70000 แผนกอื่น (600100 กดไม่ได้)
        รอบ A1 A2 ของกองแรก แล้วตามด้วยรอบ B1 B2 ของกองที่สอง — รอบใหม่สุดจึงเป็นของกองที่ 600100 กดไม่ได้"""
        from models.finance_petty_model import FinPettyBatch, FinPettyFund
        line["70000"] = _person("70000", 9, HOLDER_LEVEL, dept=99, division=99)
        other = FinPettyFund(branch_code="02", division_code="C01", holder_employee_id="70000")
        db_session.add(other)
        db_session.commit()
        rows = {}
        for name, f in (("A1", fund), ("A2", fund), ("B1", other), ("B2", other)):
            rows[name] = FinPettyBatch(fund_id=f.id, batch_no=f"X-{name}", status="CLEAR_PENDING",
                                       clear_required_level=5)
            db_session.add(rows[name])
            db_session.commit()
        return rows

    def _queue(self, db_session, emp, limit, offset):
        return R.list_rounds(fund_id=None, status=None, holder=None, clear_approver=emp,
                             limit=limit, offset=offset, db=db_session)

    def test_head_queue_pages_over_the_rounds_they_may_clear(self, db_session, two_funds):
        assert [r["batch_no"] for r in self._queue(db_session, "600100", 1, 0)] == ["X-A2"]
        assert [r["batch_no"] for r in self._queue(db_session, "600100", 1, 1)] == ["X-A1"]
        assert self._queue(db_session, "600100", 1, 2) == []
        assert [r["batch_no"] for r in self._queue(db_session, "600100", 50, 0)] == ["X-A2", "X-A1"]

    def test_claim_count_comes_from_a_count(self, db_session, claim, sent_round):
        [row] = R.list_rounds(fund_id=None, status=None, holder=None, clear_approver=None,
                              limit=50, offset=0, db=db_session)
        assert row["claim_count"] == 1 and row["id"] == sent_round["id"]

    def test_row_shape_is_unchanged(self, db_session, claim, sent_round):
        [row] = R.list_rounds(fund_id=None, status=None, holder=None, clear_approver=None,
                              limit=50, offset=0, db=db_session)
        detail = R.get_round(sent_round["id"], employee_id=None, db=db_session)
        assert set(row) == set(detail) - {"logs", "claims"}
        assert row == {k: detail[k] for k in row}

    def _count_queries(self, db_session, call):
        from sqlalchemy import event
        seen = []

        def count(*args, **kwargs):
            seen.append(1)

        engine = db_session.get_bind()
        event.listen(engine, "before_cursor_execute", count)
        try:
            call()
        finally:
            event.remove(engine, "before_cursor_execute", count)
        return len(seen)

    def test_query_count_does_not_grow_with_the_rows(self, db_session, two_funds):
        from models.finance_petty_model import FinPettyBatch

        def plain():
            return R.list_rounds(fund_id=None, status=None, holder=None, clear_approver=None,
                                 limit=50, offset=0, db=db_session)

        def queue():
            return self._queue(db_session, "600100", 50, 0)

        before = (self._count_queries(db_session, plain), self._count_queries(db_session, queue))
        fund_id = two_funds["A1"].fund_id
        for n in range(6):
            db_session.add(FinPettyBatch(fund_id=fund_id, batch_no=f"X-M{n}", status="CLEAR_PENDING",
                                         clear_required_level=5, submitted_by="12345"))
        db_session.commit()
        after = (self._count_queries(db_session, plain), self._count_queries(db_session, queue))
        assert len(plain()) == 10 and len(queue()) == 8
        assert after == before


class TestRoundsTheHeadAlreadyDecided:
    """แถบ "อนุมัติ/ไม่อนุมัติแล้ว" ของหัวหน้า: cleared_by = รอบที่คนนั้นตัดสินไปแล้ว
    (คนไม่อนุมัติก็เก็บใน clear_approved_by) · ตัดสินล่าสุดก่อน · หน้าตาแถวเดียวกับรายการรอบ"""

    @pytest.fixture
    def decided(self, db_session, fund):
        from datetime import datetime, timezone
        from models.finance_petty_model import FinPettyBatch

        def at(day):
            return datetime(2026, 10, day, 9, 0, tzinfo=timezone.utc)

        specs = [
            ("D-OLD", "CLOSED", "600100", at(1)),
            ("D-APPROVED", "AWAITING_REVIEW", "600100", at(5)),
            ("D-REJECTED", "CLEAR_REJECTED", "600100", at(9)),
            ("D-PENDING", "CLEAR_PENDING", None, None),
            ("D-ODD", "CLEAR_PENDING", "600100", at(10)),  # ยังรออยู่ ไม่นับว่าตัดสินแล้ว
            ("D-OTHER", "REVIEWED", "590100", at(7)),
        ]
        for no, status, by, when in specs:
            db_session.add(FinPettyBatch(fund_id=fund.id, batch_no=no, status=status, clear_required_level=5,
                                         clear_approved_by=by, clear_approved_at=when))
            db_session.commit()

    def _list(self, db_session, limit=50, offset=0, **kw):
        return R.list_rounds(fund_id=kw.get("fund_id"), status=kw.get("status"), holder=None,
                             clear_approver=None, cleared_by=kw.get("cleared_by", "600100"),
                             limit=limit, offset=offset, db=db_session)

    def test_approved_and_rejected_rounds_latest_decision_first(self, db_session, decided):
        assert [r["batch_no"] for r in self._list(db_session)] == ["D-REJECTED", "D-APPROVED", "D-OLD"]

    def test_pages_with_limit_and_offset(self, db_session, decided):
        assert [r["batch_no"] for r in self._list(db_session, limit=2)] == ["D-REJECTED", "D-APPROVED"]
        assert [r["batch_no"] for r in self._list(db_session, limit=2, offset=2)] == ["D-OLD"]

    def test_status_filter_still_applies(self, db_session, decided):
        assert [r["batch_no"] for r in self._list(db_session, status="CLEAR_REJECTED")] == ["D-REJECTED"]

    def test_someone_who_decided_nothing_gets_nothing(self, db_session, decided):
        assert self._list(db_session, cleared_by="600200") == []

    def test_same_row_shape_as_the_plain_list(self, db_session, decided):
        plain = R.list_rounds(fund_id=None, status=None, holder=None, clear_approver=None,
                              limit=50, offset=0, db=db_session)
        mine = self._list(db_session)
        assert set(mine[0]) == set(plain[0]) and "is_primary" not in mine[0]


class TestHolderBankAccountForThePayBox:
    """กล่องจ่ายเงินของรอบต้องเห็นบัญชีของเจ้าของเงินสด · BE ไม่มี auth จึงส่งเฉพาะ GET รายละเอียดรอบ
    และเฉพาะคนดูที่เป็นฝ่ายการเงิน/บัญชี (FINANCE_DEPARTMENT_IDS ค่าเริ่มต้น 4,6) เจ้าของเงินสดเอง หรือ superadmin
    คนอื่น ไม่ส่ง employee_id รายการ และคำตอบของปุ่ม (POST) ไม่มีคีย์ holder_payee เลย"""

    ACCOUNT = {"payee_account_type": "bank", "bank": "KBANK", "account_no": "0123456789",
               "account_name": "ณรงค์กรณ์ อัจฉริยญาติ"}

    def _account(self, db_session, status="ACTIVE"):
        from models.finance_model import FinPayeeAccount
        db_session.add(FinPayeeAccount(employee_id="12345", bank="KBANK", account_no="0123456789",
                                       account_name="ณรงค์กรณ์ อัจฉริยญาติ", status=status))
        db_session.commit()

    @pytest.fixture
    def staff(self, db_session, monkeypatch):
        """40001 การเงิน (แผนก 4) · 60001 บัญชี (แผนก 6) · 30001 แผนกอื่น"""
        from models.user_model import User
        monkeypatch.delenv("FINANCE_DEPARTMENT_IDS", raising=False)
        monkeypatch.delenv("FINANCE_EMPLOYEE_IDS", raising=False)
        for eid, dept in (("40001", 4), ("60001", 6), ("30001", 30)):
            db_session.add(User(username=f"u{eid}", password_hash="x", email=f"{eid}@test.local",
                                employee_id=eid, firstname="ก", lastname="ข", department_id=dept))
        db_session.commit()

    def _detail(self, db_session, round_id, employee_id):
        return R.get_round(round_id, employee_id=employee_id, db=db_session)

    def test_finance_sees_the_active_account(self, db_session, sent_round, staff):
        self._account(db_session)
        assert self._detail(db_session, sent_round["id"], "40001")["holder_payee"] == self.ACCOUNT

    def test_accounting_sees_it_too(self, db_session, sent_round, staff):
        self._account(db_session)
        assert self._detail(db_session, sent_round["id"], "60001")["holder_payee"] == self.ACCOUNT

    def test_the_holder_sees_their_own_account(self, db_session, sent_round, staff):
        self._account(db_session)
        assert self._detail(db_session, sent_round["id"], "12345")["holder_payee"] == self.ACCOUNT

    def test_superadmin_sees_it(self, db_session, sent_round, staff):
        self._account(db_session)
        assert self._detail(db_session, sent_round["id"], "670108")["holder_payee"] == self.ACCOUNT

    def test_someone_else_gets_no_key_at_all(self, db_session, sent_round, staff):
        self._account(db_session)
        for viewer in ("30001", "600100", "99999", None):
            body = self._detail(db_session, sent_round["id"], viewer)
            assert "holder_payee" not in body and "0123456789" not in str(body)

    def test_finance_departments_come_from_the_env(self, db_session, sent_round, staff, monkeypatch):
        self._account(db_session)
        monkeypatch.setenv("FINANCE_DEPARTMENT_IDS", "30")
        assert self._detail(db_session, sent_round["id"], "30001")["holder_payee"] == self.ACCOUNT
        assert "holder_payee" not in self._detail(db_session, sent_round["id"], "40001")

    def test_named_finance_employees_come_from_the_env(self, db_session, sent_round, staff, monkeypatch):
        self._account(db_session)
        monkeypatch.setenv("FINANCE_EMPLOYEE_IDS", "30001")
        assert self._detail(db_session, sent_round["id"], "30001")["holder_payee"] == self.ACCOUNT

    def test_inactive_account_is_not_offered(self, db_session, sent_round, staff):
        self._account(db_session, status="INACTIVE")
        assert self._detail(db_session, sent_round["id"], "40001")["holder_payee"] is None

    def test_no_account_gives_none(self, db_session, sent_round, staff):
        assert self._detail(db_session, sent_round["id"], "40001")["holder_payee"] is None

    def test_button_responses_never_carry_it(self, db_session, claim, sent_round, staff):
        self._account(db_session)
        assert "holder_payee" not in sent_round  # ส่งเคลียร์ (เจ้าของเงินสดกดเอง ก็ไม่ส่ง)
        steps = [
            R.clear_approve_round(sent_round["id"], RoundActionIn(employee_id="600100"), db_session),
            R.review_claim(claim.form_id, ReviewIn(employee_id="60001", action="PASS"), db_session),
            R.voucher_round(sent_round["id"], VoucherIn(employee_id="60001", voucher_no="PV-1",
                                                        voucher_date="2026-10-29"), db_session),
            R.edit_round_doc_numbers(sent_round["id"], DocNumbersIn(employee_id="60001", voucher_no="PV-2"),
                                     db_session),
            R.pay_round(sent_round["id"], PayIn(employee_id="40001", pay_date="2026-10-30"), db_session),
        ]
        for body in steps:
            assert "holder_payee" not in body and "0123456789" not in str(body)

    def test_reject_response_does_not_carry_it(self, db_session, sent_round, staff):
        self._account(db_session)
        out = R.clear_reject_round(sent_round["id"], RoundRejectIn(employee_id="600100", reason="x"), db_session)
        assert "holder_payee" not in out

    def test_list_never_carries_account_numbers(self, db_session, sent_round, staff):
        self._account(db_session)
        rows = R.list_rounds(fund_id=None, status=None, holder="12345", clear_approver=None,
                             limit=50, offset=0, db=db_session)
        assert rows and "holder_payee" not in rows[0] and "0123456789" not in str(rows)


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


@pytest.fixture
def vouchered_round(db_session, claim, sent_round):
    R.clear_approve_round(sent_round["id"], RoundActionIn(employee_id="600100"), db_session)
    R.review_claim(claim.form_id, ReviewIn(employee_id="80001", action="PASS"), db_session)
    return R.voucher_round(sent_round["id"], VoucherIn(employee_id="80001", voucher_no="PV-1",
                                                       voucher_date="2026-10-29"), db_session)


def _edit(round_id, db_session, **body):
    return R.edit_round_doc_numbers(round_id, DocNumbersIn(employee_id="80001", **body), db_session)


def _claim_logs(db_session, claim, action):
    from models.finance_petty_model import FinPettyLog
    return (db_session.query(FinPettyLog)
            .filter(FinPettyLog.claim_id == claim.id, FinPettyLog.action == action).all())


class TestEditRoundDocNumbers:
    """แก้เลขที่พิมพ์ผิดหลังตั้งเบิก/ทำจ่าย แบบปุ่มแก้ไขเลขเอกสารของ Advance — ไม่เปลี่ยนสถานะ"""

    def test_fix_the_voucher_number_and_date(self, db_session, claim, vouchered_round):
        out = _edit(vouchered_round["id"], db_session, voucher_no=" PV-2 ", voucher_date="2026-10-30")
        assert out["status"] == "VOUCHERED"
        assert out["voucher_no"] == "PV-2" and out["voucher_date"] == "2026-10-30"
        [row] = _claim_logs(db_session, claim, "EDIT_DOC_NUMBERS")
        assert row.action_by == "80001"
        assert row.remark == "เลขที่ตั้งเบิก: PV-1 → PV-2 · วันที่ตั้งเบิก: 29/10/2026 → 30/10/2026"
        assert row.changes["voucher_no"] == ["PV-1", "PV-2"]

    def test_pay_number_cannot_be_set_before_paying(self, db_session, vouchered_round):
        with pytest.raises(HTTPException) as exc:
            _edit(vouchered_round["id"], db_session, pay_doc_no="PAY-1")
        assert exc.value.status_code == 409 and "จ่าย" in exc.value.detail

    def test_an_empty_pay_number_before_paying_is_fine(self, db_session, vouchered_round):
        # หน้าเว็บอาจส่งทุกช่องมาทุกครั้ง: ช่องว่างที่ไม่ได้เปลี่ยนอะไรต้องไม่ถูกปัดตก
        out = _edit(vouchered_round["id"], db_session, voucher_no="PV-2", pay_doc_no=None)
        assert out["voucher_no"] == "PV-2" and out["pay_doc_no"] is None

    def test_after_paying_every_number_can_be_fixed(self, db_session, claim, vouchered_round):
        R.pay_round(vouchered_round["id"], PayIn(employee_id="90001", pay_date="2026-10-30", pay_doc_no="PAY-1"),
                    db_session)
        out = _edit(vouchered_round["id"], db_session, voucher_no="", pay_doc_no="PAY-2")
        assert out["status"] == "CLOSED" and out["voucher_no"] is None and out["pay_doc_no"] == "PAY-2"
        [row] = _claim_logs(db_session, claim, "EDIT_DOC_NUMBERS")
        assert row.remark == "เลขที่ตั้งเบิก: PV-1 → - · เลขที่ทำจ่าย: PAY-1 → PAY-2"

    def test_not_before_the_voucher(self, db_session, sent_round):
        with pytest.raises(HTTPException) as exc:
            _edit(sent_round["id"], db_session, voucher_no="PV-2")
        assert exc.value.status_code == 409 and "ตั้งเบิก" in exc.value.detail

    def test_voucher_date_cannot_be_removed(self, db_session, vouchered_round):
        with pytest.raises(HTTPException) as exc:
            _edit(vouchered_round["id"], db_session, voucher_date=None)
        assert exc.value.status_code == 422

    def test_nothing_changed_writes_no_log(self, db_session, claim, vouchered_round):
        out = _edit(vouchered_round["id"], db_session, voucher_no="PV-1", voucher_date="2026-10-29")
        assert out["voucher_no"] == "PV-1" and _claim_logs(db_session, claim, "EDIT_DOC_NUMBERS") == []

    def test_unknown_round_is_404(self, db_session):
        with pytest.raises(HTTPException) as exc:
            _edit(9999, db_session, voucher_no="PV-2")
        assert exc.value.status_code == 404

    def test_numbers_are_capped_at_50(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            DocNumbersIn(employee_id="80001", pay_doc_no="X" * 51)


class TestRoundHistory:
    """ประวัติรอบ (เก่าสุดก่อน) สร้างจากช่องของรอบเอง + การแก้เลขเอกสาร — ไม่ต้องมีตาราง log ของรอบ"""

    def _actions(self, body):
        return [row["action"] for row in body["logs"]]

    def test_a_new_round_starts_with_the_submission(self, db_session, claim, line):
        _approve(db_session, claim)
        R.set_claim_dd(claim.form_id, DdIn(employee_id="12345", dd_no="LBDD26090931"), db_session)
        out = R.create_round(_round_in(claim, remark="รอบปลายเดือน"), db_session)
        [row] = out["logs"]
        assert set(row) >= {"action", "actor", "actor_name", "at", "remark"}
        assert row["action"] == "SUBMIT_ROUND" and row["actor"] == "12345" and row["remark"] == "รอบปลายเดือน"
        assert row["at"] == out["submitted_at"]

    def test_the_whole_path_in_order_with_names(self, db_session, claim, vouchered_round):
        _user(db_session, "80001", "บัญชี", "ตรวจดี")
        _edit(vouchered_round["id"], db_session, voucher_no="PV-2")
        R.pay_round(vouchered_round["id"], PayIn(employee_id="90001", pay_date="2026-10-30", pay_doc_no="PAY-1"),
                    db_session)
        body = R.get_round(vouchered_round["id"], employee_id=None, db=db_session)
        assert self._actions(body) == ["SUBMIT_ROUND", "CLEAR_APPROVE", "VOUCHER", "EDIT_DOC_NUMBERS",
                                       "PAY", "CLOSE"]
        rows = {row["action"]: row for row in body["logs"]}
        assert rows["CLEAR_APPROVE"]["actor"] == "600100"
        assert rows["VOUCHER"]["actor_name"] == "บัญชี ตรวจดี"
        # ขั้นตั้งเบิกโชว์เลขที่บันทึกตอนนั้น (PV-1) ค่าที่แก้ทีหลังอยู่ในแถวแก้เลขเอกสาร
        assert rows["VOUCHER"]["remark"] == "เลขที่ตั้งเบิก PV-1 · วันที่ตั้งเบิก 29/10/2026"
        assert rows["EDIT_DOC_NUMBERS"]["remark"] == "เลขที่ตั้งเบิก: PV-1 → PV-2"
        assert rows["PAY"]["actor"] == "90001"
        assert rows["PAY"]["remark"] == "เลขที่ทำจ่าย PAY-1 · วันที่โอนเงิน 30/10/2026"
        assert rows["CLOSE"]["at"] == body["closed_at"]
        assert body["voucher_no"] == "PV-2"  # ตัวรอบเองยังเป็นค่าล่าสุด

    def test_steps_keep_the_numbers_saved_at_the_time_after_later_edits(self, db_session, claim,
                                                                         vouchered_round):
        R.pay_round(vouchered_round["id"], PayIn(employee_id="90001", pay_date="2026-10-30", pay_doc_no="PAY-1"),
                    db_session)
        _edit(vouchered_round["id"], db_session, voucher_no="PV-9", voucher_date="2026-10-31", pay_doc_no="PAY-9")
        _edit(vouchered_round["id"], db_session, pay_doc_no="PAY-10")
        body = R.get_round(vouchered_round["id"], employee_id=None, db=db_session)
        rows = {row["action"]: row for row in body["logs"]}
        assert rows["VOUCHER"]["remark"] == "เลขที่ตั้งเบิก PV-1 · วันที่ตั้งเบิก 29/10/2026"
        assert rows["PAY"]["remark"] == "เลขที่ทำจ่าย PAY-1 · วันที่โอนเงิน 30/10/2026"
        assert [r["action"] for r in body["logs"]].count("EDIT_DOC_NUMBERS") == 2
        assert body["pay_doc_no"] == "PAY-10" and body["voucher_date"] == "2026-10-31"

    def test_without_claim_logs_the_first_edit_tells_the_saved_number(self):
        # รอบเก่าที่ไม่มี log ตั้งเบิก/จ่ายในใบ: ค่าตอนนั้น = ค่าเดิมของการแก้ครั้งแรก · ไม่เคยแก้ = ค่าบนรอบ
        from datetime import date
        saved = L.saved_doc_values(None, None, [
            {"voucher_no": ["PV-1", "PV-2"], "voucher_date": ["2026-10-29", "2026-10-30"]},
            {"voucher_no": ["PV-2", "PV-3"]},
        ])
        assert saved == {"voucher_no": "PV-1", "voucher_date": date(2026, 10, 29)}
        saved = L.saved_doc_values({"voucher_no": None, "voucher_date": "2026-10-29"},
                                   {"pay_date": "2026-10-30", "pay_doc_no": "PAY-1"}, [])
        assert saved == {"voucher_no": None, "voucher_date": date(2026, 10, 29),
                         "pay_date": date(2026, 10, 30), "pay_doc_no": "PAY-1"}

    def test_one_edit_shows_once_however_many_claims_the_round_has(self, db_session, fund, weigh_type,
                                                                   claim_payload, weigh_items, line,
                                                                   seq_stub, frozen_bkk):
        claims = [repo.create_claim(db_session, claim_payload(), weigh_items[:1], actor="55555") for _ in range(2)]
        for c in claims:
            repo.set_status(db_session, c, L.APPROVED, "12345", "APPROVE")
        batch = repo.create_round(db_session, fund.id, [c.id for c in claims], actor="12345", remark=None,
                                  required_level=5)
        repo.clear_approve_round(db_session, batch, actor="600100")
        for c in claims:
            repo.review_pass(db_session, c, actor="80001", remark=None)
        db_session.refresh(batch)
        repo.voucher_round(db_session, batch, actor="80001", voucher_no="PV-1", voucher_date=L.bkk_today())
        _edit(batch.id, db_session, voucher_no="PV-2")
        body = R.get_round(batch.id, employee_id=None, db=db_session)
        assert self._actions(body).count("EDIT_DOC_NUMBERS") == 1

    def test_a_rejected_round_ends_with_the_reason(self, db_session, sent_round):
        out = R.clear_reject_round(sent_round["id"], RoundRejectIn(employee_id="600100", reason="บิลไม่ครบ"),
                                   db_session)
        assert self._actions(out) == ["SUBMIT_ROUND", "CLEAR_REJECT"]
        assert out["logs"][-1]["remark"] == "บิลไม่ครบ" and out["logs"][-1]["actor"] == "600100"

    def test_voucher_without_a_number_still_shows_the_date(self, db_session, claim, sent_round):
        R.clear_approve_round(sent_round["id"], RoundActionIn(employee_id="600100"), db_session)
        R.review_claim(claim.form_id, ReviewIn(employee_id="80001", action="PASS"), db_session)
        out = R.voucher_round(sent_round["id"], VoucherIn(employee_id="80001", voucher_date="2026-10-29"),
                              db_session)
        assert out["logs"][-1]["remark"] == "วันที่ตั้งเบิก 29/10/2026"

    def test_list_rows_do_not_carry_history(self, db_session, sent_round):
        rows = R.list_rounds(fund_id=None, status=None, holder=None, clear_approver=None,
                             limit=50, offset=0, db=db_session)
        assert "logs" not in rows[0]


class TestRoundDocNumberDuplicates:
    """เตือนเลขที่ตั้งเบิก / เลขที่ทำจ่ายซ้ำกับรอบอื่นแบบ Advance — เตือนอย่างเดียว ไม่บล็อก"""

    @pytest.fixture
    def rounds(self, db_session, fund):
        from models.finance_petty_model import FinPettyBatch
        rows = [FinPettyBatch(fund_id=fund.id, batch_no="LB-B02-2610-B1", status="CLOSED",
                              voucher_no="pv-6910-001", pay_doc_no="PAY-1"),
                FinPettyBatch(fund_id=fund.id, batch_no="LB-B02-2610-B2", status="VOUCHERED",
                              voucher_no=" PV-6910-001 "),
                FinPettyBatch(fund_id=fund.id, batch_no="LB-B02-2610-B3", status="VOUCHERED",
                              voucher_no="PV-6910-002")]
        db_session.add_all(rows)
        db_session.commit()
        return rows

    def test_route_is_matched_before_the_batch_id_route(self):
        paths = [r.path for r in R.router.routes]
        assert paths.index("/finance/petty/batches/duplicate-doc-no") < \
               paths.index("/finance/petty/batches/{batch_id}")

    def test_finds_other_rounds_ignoring_case_and_spaces(self, db_session, rounds):
        out = R.round_duplicate_doc_no(field="voucher_no", value="  Pv-6910-001", exclude_id=rounds[2].id,
                                       db=db_session)
        assert out["batch_nos"] == ["LB-B02-2610-B1", "LB-B02-2610-B2"]

    def test_the_round_being_edited_is_left_out(self, db_session, rounds):
        out = R.round_duplicate_doc_no(field="voucher_no", value="PV-6910-001", exclude_id=rounds[1].id,
                                       db=db_session)
        assert out["batch_nos"] == ["LB-B02-2610-B1"]

    def test_pay_doc_no_is_checked_too(self, db_session, rounds):
        out = R.round_duplicate_doc_no(field="pay_doc_no", value="pay-1", exclude_id=None, db=db_session)
        assert out["batch_nos"] == ["LB-B02-2610-B1"]

    def test_blank_value_finds_nothing(self):
        assert R.round_duplicate_doc_no(field="voucher_no", value="  ", exclude_id=None, db=None)["batch_nos"] == []

    def test_other_fields_are_refused(self):
        with pytest.raises(HTTPException) as exc:
            R.round_duplicate_doc_no(field="batch_no", value="x", exclude_id=None, db=None)
        assert exc.value.status_code == 400


class TestRoundRoutesOverHttp:
    """ยิง HTTP จริงผ่าน TestClient (แอปเล็กที่มีแค่ router ของ petty ไม่ต้อง import main)
    พิสูจน์ว่า /batches/duplicate-doc-no ไม่โดน /batches/{batch_id} จับไปก่อน — ถ้าโดน จะได้ 422 (ไม่ใช่ตัวเลข)"""

    @pytest.fixture
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        import database
        from database import Base
        import models.finance_petty_model as petty

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine, tables=[petty.FinPettyFund.__table__, petty.FinPettyBatch.__table__,
                                                 petty.FinPettyClaim.__table__, petty.FinPettyLog.__table__])
        Session = sessionmaker(bind=engine)
        with Session() as db:
            f = petty.FinPettyFund(branch_code="03", division_code="B02", holder_employee_id="12345")
            db.add(f)
            db.flush()
            db.add(petty.FinPettyBatch(fund_id=f.id, batch_no="LB-B02-2610-B1", status="CLOSED",
                                       voucher_no="PV-6910-001"))
            db.commit()

        def _get_db():
            db = Session()
            try:
                yield db
            finally:
                db.close()

        app = FastAPI()
        app.include_router(R.router)
        app.dependency_overrides[database.get_db] = _get_db
        yield TestClient(app)
        engine.dispose()

    def test_duplicate_doc_no_answers_over_http(self, client):
        res = client.get("/finance/petty/batches/duplicate-doc-no",
                         params={"field": "voucher_no", "value": " pv-6910-001 "})
        assert res.status_code == 200, res.text
        assert res.json() == {"field": "voucher_no", "value": "pv-6910-001", "batch_nos": ["LB-B02-2610-B1"]}

    def test_duplicate_doc_no_refuses_other_fields_over_http(self, client):
        res = client.get("/finance/petty/batches/duplicate-doc-no", params={"field": "batch_no", "value": "x"})
        assert res.status_code == 400

    def test_a_numeric_round_id_still_reaches_the_detail_route(self, client):
        assert client.get("/finance/petty/batches/9999").status_code == 404


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
