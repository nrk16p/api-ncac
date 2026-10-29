"""รอบส่งเคลียร์ (flow v2 29/10/2026)

เจ้าของเงินสดรวมใบที่อนุมัติจ่ายแล้วเป็นรอบ → หัวหน้าอนุมัติเคลียร์ทั้งรอบ → บัญชีตรวจทีละใบ
→ ตรวจผ่านครบทั้งรอบ → บัญชีตั้งเบิก → การเงินทำจ่าย → ปิดทั้งรอบ
"""
from datetime import date
from decimal import Decimal

import pytest

from models.finance_petty_model import FinPettyFund, FinPettyLog
from services.finance import petty_logic as L
from services.finance import petty_repo as repo


@pytest.fixture
def approved(db_session, fund, claim_payload, weigh_items, seq_stub, frozen_bkk):
    """ใบที่เจ้าของเงินสด (12345) อนุมัติจ่ายแล้ว — สร้างใหม่ทุกครั้งที่เรียก"""
    def _make(items=None):
        claim = repo.create_claim(db_session, claim_payload(), items or weigh_items, actor="55555")
        return repo.set_status(db_session, claim, L.APPROVED, "12345", "APPROVE",
                               fields={"approved_by": "12345", "transfer_date": date(2026, 9, 25)})
    return _make


def _round(db_session, fund, claims, required=5):
    return repo.create_round(db_session, fund.id, [c.id for c in claims], actor="12345", remark=None,
                             required_level=required)


def _actions(db_session, claim):
    return [row.action for row in db_session.query(FinPettyLog)
            .filter(FinPettyLog.claim_id == claim.id).order_by(FinPettyLog.id).all()]


def _last_log(db_session, claim):
    return (db_session.query(FinPettyLog).filter(FinPettyLog.claim_id == claim.id)
            .order_by(FinPettyLog.id.desc()).first())


class TestCreateRound:
    def test_puts_every_claim_into_the_round(self, db_session, fund, approved):
        a, b = approved(), approved()
        batch = _round(db_session, fund, [a, b])
        assert batch.status == "CLEAR_PENDING" and batch.total_amount == Decimal("3280.00")
        assert batch.submitted_by == "12345" and batch.submitted_at is not None
        assert batch.clear_required_level == 5 and batch.closed_at is None
        assert {a.status, b.status} == {"CLEAR_PENDING"} and a.batch_id == b.batch_id == batch.id
        assert _actions(db_session, a)[-1] == "SUBMIT_ROUND"

    def test_round_number_runs_per_fund_and_month(self, db_session, fund, approved):
        first = _round(db_session, fund, [approved()])
        second = _round(db_session, fund, [approved()])
        assert first.batch_no == "LB-B02-2610-B1" and second.batch_no == "LB-B02-2610-B2"

    def test_round_number_runs_per_branch_division_not_per_fund(self, db_session, fund, approved,
                                                                claim_payload, weigh_items):
        # เลขรอบ unique ทั้งตาราง แต่กองทุนซ้ำสาขา+แผนกได้เมื่อกองเก่าปิดไปแล้ว (เปลี่ยนเจ้าของเงินสด)
        # เดิมนับรอบต่อกองทุน กองใหม่จึงได้ B1 ชนกับรอบของกองเก่า
        _round(db_session, fund, [approved()])
        fund.is_active = False
        newer = FinPettyFund(branch_code="03", division_code="B02", holder_employee_id="22222")
        db_session.add(newer)
        db_session.commit()
        claim = repo.create_claim(db_session, claim_payload(fund_id=newer.id), weigh_items, actor="55555")
        repo.set_status(db_session, claim, L.APPROVED, "22222", "APPROVE")
        batch = repo.create_round(db_session, newer.id, [claim.id], actor="22222", remark=None, required_level=5)
        assert batch.batch_no == "LB-B02-2610-B2"

    def test_round_number_follows_the_highest_not_the_count(self, db_session, fund, approved):
        from models.finance_petty_model import FinPettyBatch
        db_session.add(FinPettyBatch(fund_id=fund.id, batch_no="LB-B02-2610-B7", status="CLOSED"))
        db_session.add(FinPettyBatch(fund_id=fund.id, batch_no="LB-B02-2609-B9", status="CLOSED"))
        db_session.add(FinPettyBatch(fund_id=fund.id, batch_no="LB-B021-2610-B5", status="CLOSED"))
        db_session.commit()
        assert _round(db_session, fund, [approved()]).batch_no == "LB-B02-2610-B8"

    def test_underscore_in_the_division_is_not_a_wildcard(self, db_session, fund, approved):
        # LIKE ถือ _ เป็นตัวอักษรอะไรก็ได้ รอบของแผนก BX2 ต้องไม่ถูกนับเป็นของแผนก B_2
        from models.finance_petty_model import FinPettyBatch
        fund.division_code = "B_2"
        db_session.add(FinPettyBatch(fund_id=fund.id, batch_no="LB-BX2-2610-B9", status="CLOSED"))
        db_session.add(FinPettyBatch(fund_id=fund.id, batch_no="LB-B_2-2610-B3", status="CLOSED"))
        db_session.commit()
        assert _round(db_session, fund, [approved()]).batch_no == "LB-B_2-2610-B4"
        assert repo._last_round_number(db_session, "03", "BX2", 2610) == 9

    def test_claim_not_yet_approved_is_rejected(self, db_session, fund, claim_payload, weigh_items,
                                                seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        with pytest.raises(L.PettyRuleError) as exc:
            _round(db_session, fund, [claim])
        assert exc.value.http_status == 409 and claim.form_id in str(exc.value)

    def test_claim_already_in_a_round_is_rejected(self, db_session, fund, approved):
        a = approved()
        _round(db_session, fund, [a])
        with pytest.raises(L.PettyRuleError):
            _round(db_session, fund, [a])

    def test_claim_from_another_fund_is_rejected(self, db_session, fund, approved):
        other = FinPettyFund(branch_code="01", division_code="A01", holder_employee_id="22222")
        db_session.add(other)
        db_session.commit()
        a = approved()
        with pytest.raises(L.PettyRuleError) as exc:
            repo.create_round(db_session, other.id, [a.id], actor="22222", remark=None, required_level=5)
        assert exc.value.http_status == 409

    def test_missing_claim_id_is_404(self, db_session, fund, approved):
        with pytest.raises(L.PettyRuleError) as exc:
            repo.create_round(db_session, fund.id, [approved().id, 9999], actor="12345", remark=None,
                              required_level=5)
        assert exc.value.http_status == 404

    def test_unknown_fund_raises(self, db_session, fund, approved):
        with pytest.raises(LookupError):
            repo.create_round(db_session, fund.id + 99, [approved().id], actor="12345", remark=None,
                              required_level=5)


class TestHeadDecision:
    def test_approve_sends_the_whole_round_to_accounting(self, db_session, fund, approved):
        a, b = approved(), approved()
        batch = _round(db_session, fund, [a, b])
        out = repo.clear_approve_round(db_session, batch, actor="600100")
        assert out.status == "AWAITING_REVIEW" and out.clear_approved_by == "600100"
        assert out.clear_approved_at is not None
        assert {a.status, b.status} == {"AWAITING_REVIEW"} and a.submitted_to_acc_at is not None
        assert _actions(db_session, a)[-1] == "CLEAR_APPROVE"

    def test_cannot_approve_twice(self, db_session, fund, approved):
        batch = _round(db_session, fund, [approved()])
        repo.clear_approve_round(db_session, batch, actor="600100")
        with pytest.raises(L.PettyRuleError) as exc:
            repo.clear_approve_round(db_session, batch, actor="600100")
        assert exc.value.http_status == 409

    def test_reject_hands_the_claims_back_to_the_holder(self, db_session, fund, approved):
        a = approved()
        batch = _round(db_session, fund, [a])
        out = repo.clear_reject_round(db_session, batch, actor="600100", reason="สลิปไม่ชัด")
        assert out.status == "CLEAR_REJECTED" and out.clear_reject_reason == "สลิปไม่ชัด"
        assert a.status == "APPROVED" and a.batch_id is None
        assert _actions(db_session, a)[-1] == "CLEAR_REJECT"

    def test_rejected_claims_can_go_into_a_new_round(self, db_session, fund, approved):
        a = approved()
        repo.clear_reject_round(db_session, _round(db_session, fund, [a]), actor="600100", reason="x")
        again = _round(db_session, fund, [a])
        assert again.batch_no.endswith("-B2") and a.batch_id == again.id

    def test_rejected_claims_still_hold_the_money(self, db_session, fund, approved):
        a = approved()
        repo.clear_reject_round(db_session, _round(db_session, fund, [a]), actor="600100", reason="x")
        assert repo.petty_committed(db_session, "12345") == Decimal("1640.00")


class TestAccountingReview:
    def _in_review(self, db_session, fund, approved, n=2):
        claims = [approved() for _ in range(n)]
        batch = repo.clear_approve_round(db_session, _round(db_session, fund, claims), actor="600100")
        return batch, claims

    def test_round_waits_until_every_claim_passes(self, db_session, fund, approved):
        batch, (a, b) = self._in_review(db_session, fund, approved)
        repo.review_pass(db_session, a, actor="80001", remark=None)
        assert a.status == "REVIEWED" and a.reviewed_by == "80001"
        db_session.refresh(batch)
        assert batch.status == "AWAITING_REVIEW"
        repo.review_pass(db_session, b, actor="80001", remark="ครบ")
        db_session.refresh(batch)
        assert batch.status == "REVIEWED"

    def test_sent_back_claim_stays_in_the_round(self, db_session, fund, approved):
        batch, (a, b) = self._in_review(db_session, fund, approved)
        repo.set_status(db_session, a, L.SENT_BACK, "80001", "SEND_BACK", fields={"review_remark": "บิลเบลอ"})
        repo.review_pass(db_session, b, actor="80001", remark=None)
        db_session.refresh(batch)
        assert a.batch_id == batch.id and batch.status == "AWAITING_REVIEW"

    def test_clerk_fixes_a_sent_back_claim_and_it_goes_straight_back_to_accounting(
            self, db_session, fund, approved, weigh_items):
        batch, (a, b) = self._in_review(db_session, fund, approved)
        repo.set_status(db_session, a, L.SENT_BACK, "80001", "SEND_BACK", fields={"review_remark": "บิลเบลอ"})
        out = repo.update_draft(db_session, a, {}, weigh_items[:3], actor="55555")
        assert out.status == "AWAITING_REVIEW" and out.amount == Decimal("300.00")
        assert _actions(db_session, a)[-1] == "RESUBMIT_ACC"
        repo.review_pass(db_session, a, actor="80001", remark=None)
        repo.review_pass(db_session, b, actor="80001", remark=None)
        db_session.refresh(batch)
        assert batch.status == "REVIEWED"

    def test_cannot_pass_a_claim_that_is_not_awaiting_review(self, db_session, fund, approved):
        with pytest.raises(L.PettyRuleError) as exc:
            repo.review_pass(db_session, approved(), actor="80001", remark=None)
        assert exc.value.http_status == 409


class TestVoucherAndPay:
    def _reviewed_round(self, db_session, fund, approved):
        claims = [approved(), approved()]
        batch = repo.clear_approve_round(db_session, _round(db_session, fund, claims), actor="600100")
        for c in claims:
            repo.review_pass(db_session, c, actor="80001", remark=None)
        db_session.refresh(batch)
        return batch, claims

    def test_voucher_then_pay_closes_the_round_and_releases_the_money(self, db_session, fund, approved):
        batch, (a, b) = self._reviewed_round(db_session, fund, approved)
        assert repo.petty_committed(db_session, "12345") == Decimal("3280.00")
        repo.voucher_round(db_session, batch, actor="80001", voucher_no="PV-2610-001",
                           voucher_date=date(2026, 10, 29))
        assert batch.status == "VOUCHERED" and batch.voucher_no == "PV-2610-001"
        out = repo.pay_round(db_session, batch, actor="90001", pay_date=date(2026, 10, 30))
        assert out.status == "CLOSED" and out.pay_date == date(2026, 10, 30)
        assert out.paid_by == out.closed_by == "90001" and out.closed_at is not None
        assert {a.status, b.status} == {"CLOSED"}
        assert repo.petty_committed(db_session, "12345") == Decimal("0.00")
        assert _actions(db_session, a)[-1] == "PAY_CLOSE"

    def test_voucher_recounts_the_total(self, db_session, fund, approved, weigh_items):
        claims = [approved(), approved()]
        batch = repo.clear_approve_round(db_session, _round(db_session, fund, claims), actor="600100")
        repo.set_status(db_session, claims[0], L.SENT_BACK, "80001", "SEND_BACK")
        repo.update_draft(db_session, claims[0], {}, weigh_items[:1], actor="55555")
        for c in claims:
            repo.review_pass(db_session, c, actor="80001", remark=None)
        db_session.refresh(batch)
        repo.voucher_round(db_session, batch, actor="80001", voucher_no="PV-1", voucher_date=date(2026, 10, 29))
        assert batch.total_amount == Decimal("1740.00")

    def test_cannot_voucher_before_every_claim_passes(self, db_session, fund, approved):
        claims = [approved()]
        batch = repo.clear_approve_round(db_session, _round(db_session, fund, claims), actor="600100")
        with pytest.raises(L.PettyRuleError) as exc:
            repo.voucher_round(db_session, batch, actor="80001", voucher_no="PV-1", voucher_date=date(2026, 10, 29))
        assert exc.value.http_status == 409

    def test_voucher_keeps_the_date_and_each_claim_logs_it(self, db_session, fund, approved):
        batch, (a, b) = self._reviewed_round(db_session, fund, approved)
        repo.voucher_round(db_session, batch, actor="80001", voucher_no="PV-9", voucher_date=date(2026, 10, 29))
        assert batch.voucher_date == date(2026, 10, 29)
        for claim in (a, b):
            row = _last_log(db_session, claim)
            assert row.action == "VOUCHER" and row.action_by == "80001"
            assert row.changes["voucher_no"] == "PV-9" and row.changes["voucher_date"] == "2026-10-29"

    def test_voucher_number_may_be_left_blank(self, db_session, fund, approved):
        # เหมือน Advance: เลขที่ตั้งเบิกเว้นได้ วันที่ต้องมี
        batch, _ = self._reviewed_round(db_session, fund, approved)
        repo.voucher_round(db_session, batch, actor="80001", voucher_no=None, voucher_date=date(2026, 10, 29))
        assert batch.status == "VOUCHERED" and batch.voucher_no is None

    def test_pay_keeps_the_document_number_and_logs_it(self, db_session, fund, approved):
        batch, (a, _) = self._reviewed_round(db_session, fund, approved)
        repo.voucher_round(db_session, batch, actor="80001", voucher_no="PV-9", voucher_date=date(2026, 10, 29))
        out = repo.pay_round(db_session, batch, actor="90001", pay_date=date(2026, 10, 30), pay_doc_no="PAY-77")
        assert out.pay_doc_no == "PAY-77"
        row = _last_log(db_session, a)
        assert row.action == "PAY_CLOSE"
        assert row.changes["pay_doc_no"] == "PAY-77" and row.changes["pay_date"] == "2026-10-30"

    def test_pay_document_number_is_optional(self, db_session, fund, approved):
        batch, _ = self._reviewed_round(db_session, fund, approved)
        repo.voucher_round(db_session, batch, actor="80001", voucher_no="PV-9", voucher_date=date(2026, 10, 29))
        assert repo.pay_round(db_session, batch, actor="90001", pay_date=date(2026, 10, 30)).pay_doc_no is None

    def test_cannot_pay_before_voucher(self, db_session, fund, approved):
        batch, _ = self._reviewed_round(db_session, fund, approved)
        with pytest.raises(L.PettyRuleError):
            repo.pay_round(db_session, batch, actor="90001", pay_date=date(2026, 10, 30))


class TestDdNumber:
    def test_holder_adds_the_dd_while_approved(self, db_session, approved):
        a = approved()
        out = repo.set_dd(db_session, a, "LBDD26090931", actor="12345")
        assert out.dd_no == "LBDD26090931" and _actions(db_session, a)[-1] == "SET_DD"

    def test_not_after_the_round_is_sent(self, db_session, fund, approved):
        a = approved()
        _round(db_session, fund, [a])
        with pytest.raises(L.PettyRuleError) as exc:
            repo.set_dd(db_session, a, "LBDD1", actor="12345")
        assert exc.value.http_status == 409


class TestRoundIsAllOrNothing:
    """ทั้งรอบเปลี่ยนพร้อมกัน หรือไม่เปลี่ยนอะไรเลย — เดิม set_status commit ทีละใบ
    ใบที่ 2 ล้มแล้วรอบค้างครึ่งทาง: รอบเปลี่ยนสถานะไปแล้ว ใบแรกเปลี่ยน ใบที่ 2 ไม่เปลี่ยน"""

    def _tamper(self, db_session, claim, status):
        claim.status = status  # จำลองใบที่ 2 อยู่ผิดสถานะ เปลี่ยนต่อไม่ได้
        db_session.commit()

    def _fresh(self, db_session, *rows):
        db_session.expire_all()
        return rows

    def test_create_round_keeps_nothing_when_the_second_claim_fails(self, db_session, fund, approved,
                                                                    monkeypatch):
        from models.finance_petty_model import FinPettyBatch
        a, b = approved(), approved()
        real = repo.set_status

        def second_one_breaks(db, claim, *args, **kwargs):
            if claim.id == b.id:
                raise RuntimeError("ฐานข้อมูลสะดุด")
            return real(db, claim, *args, **kwargs)

        monkeypatch.setattr(repo, "set_status", second_one_breaks)
        with pytest.raises(RuntimeError):
            _round(db_session, fund, [a, b])
        self._fresh(db_session)
        assert db_session.query(FinPettyBatch).count() == 0
        assert (a.status, a.batch_id, b.status, b.batch_id) == ("APPROVED", None, "APPROVED", None)
        assert "SUBMIT_ROUND" not in _actions(db_session, a)

    def test_clear_approve_changes_nothing_when_the_second_claim_cannot_move(self, db_session, fund, approved):
        a, b = approved(), approved()
        batch = _round(db_session, fund, [a, b])
        self._tamper(db_session, b, L.APPROVED)
        with pytest.raises(L.PettyRuleError):
            repo.clear_approve_round(db_session, batch, actor="600100")
        self._fresh(db_session)
        assert batch.status == "CLEAR_PENDING" and batch.clear_approved_by is None
        assert a.status == "CLEAR_PENDING" and a.submitted_to_acc_at is None
        assert "CLEAR_APPROVE" not in _actions(db_session, a)

    def test_clear_reject_changes_nothing_when_the_second_claim_cannot_move(self, db_session, fund, approved):
        a, b = approved(), approved()
        batch = _round(db_session, fund, [a, b])
        self._tamper(db_session, b, L.AWAITING_REVIEW)
        with pytest.raises(L.PettyRuleError):
            repo.clear_reject_round(db_session, batch, actor="600100", reason="x")
        self._fresh(db_session)
        assert batch.status == "CLEAR_PENDING" and batch.clear_reject_reason is None
        assert a.status == "CLEAR_PENDING" and a.batch_id == batch.id

    def _reviewed(self, db_session, fund, approved):
        claims = [approved(), approved()]
        batch = repo.clear_approve_round(db_session, _round(db_session, fund, claims), actor="600100")
        for c in claims:
            repo.review_pass(db_session, c, actor="80001", remark=None)
        db_session.refresh(batch)
        return batch, claims

    def test_voucher_changes_nothing_when_a_claim_log_fails(self, db_session, fund, approved, monkeypatch):
        batch, (a, b) = self._reviewed(db_session, fund, approved)
        real = repo.log
        calls = []

        def second_log_breaks(db, claim_id, action, *args, **kwargs):
            calls.append(claim_id)
            if len(calls) == 2:
                raise RuntimeError("ฐานข้อมูลสะดุด")
            return real(db, claim_id, action, *args, **kwargs)

        monkeypatch.setattr(repo, "log", second_log_breaks)
        with pytest.raises(RuntimeError):
            repo.voucher_round(db_session, batch, actor="80001", voucher_no="PV-1", voucher_date=date(2026, 10, 29))
        self._fresh(db_session)
        assert batch.status == "REVIEWED" and batch.voucher_no is None and batch.vouchered_at is None
        assert "VOUCHER" not in _actions(db_session, a)

    def test_pay_changes_nothing_when_the_second_claim_cannot_close(self, db_session, fund, approved):
        batch, (a, b) = self._reviewed(db_session, fund, approved)
        repo.voucher_round(db_session, batch, actor="80001", voucher_no="PV-1", voucher_date=date(2026, 10, 29))
        self._tamper(db_session, b, L.AWAITING_REVIEW)
        with pytest.raises(L.PettyRuleError):
            repo.pay_round(db_session, batch, actor="90001", pay_date=date(2026, 10, 30), pay_doc_no="PAY-1")
        self._fresh(db_session)
        assert batch.status == "VOUCHERED" and batch.paid_at is None and batch.pay_doc_no is None
        assert a.status == "REVIEWED" and b.status == "AWAITING_REVIEW"
        assert "PAY_CLOSE" not in _actions(db_session, a)
        assert repo.petty_committed(db_session, "12345") == Decimal("3280.00")

    def test_last_pass_and_the_round_status_land_together(self, db_session, fund, approved, monkeypatch):
        claims = [approved(), approved()]
        batch = repo.clear_approve_round(db_session, _round(db_session, fund, claims), actor="600100")
        repo.review_pass(db_session, claims[0], actor="80001", remark=None)

        def round_update_breaks(db, batch, target):
            raise RuntimeError("ฐานข้อมูลสะดุด")

        monkeypatch.setattr(repo, "_set_round_status", round_update_breaks)
        with pytest.raises(RuntimeError):
            repo.review_pass(db_session, claims[1], actor="80001", remark=None)
        self._fresh(db_session)
        assert claims[1].status == "AWAITING_REVIEW" and batch.status == "AWAITING_REVIEW"


class TestTwoRoundsAtOnce:
    """สองคนส่งเคลียร์พร้อมกัน: เดิมอ่านเลขสูงสุดตัวเดียวกันแล้วชน unique ของ batch_no เป็น 500"""

    def _stale_numbers(self, monkeypatch, stale_times):
        real = repo._last_round_number
        calls = []

        def stale_then_real(db, branch, division, yymm):
            calls.append(1)
            return 0 if len(calls) <= stale_times else real(db, branch, division, yymm)

        monkeypatch.setattr(repo, "_last_round_number", stale_then_real)
        return calls

    def _taken_b1(self, db_session, fund):
        from models.finance_petty_model import FinPettyBatch
        db_session.add(FinPettyBatch(fund_id=fund.id, batch_no="LB-B02-2610-B1", status="CLEAR_PENDING"))
        db_session.commit()

    def test_a_number_taken_in_the_meantime_is_retried_once(self, db_session, fund, approved, monkeypatch):
        a = approved()
        self._taken_b1(db_session, fund)
        calls = self._stale_numbers(monkeypatch, stale_times=1)
        batch = _round(db_session, fund, [a])
        assert batch.batch_no == "LB-B02-2610-B2" and len(calls) == 2
        assert a.status == "CLEAR_PENDING" and a.batch_id == batch.id
        assert _actions(db_session, a).count("SUBMIT_ROUND") == 1

    def test_a_second_collision_is_a_plain_409(self, db_session, fund, approved, monkeypatch):
        from models.finance_petty_model import FinPettyBatch
        a = approved()
        self._taken_b1(db_session, fund)
        self._stale_numbers(monkeypatch, stale_times=99)
        with pytest.raises(L.PettyRuleError) as exc:
            _round(db_session, fund, [a])
        assert exc.value.http_status == 409 and "ส่งเคลียร์อีกครั้ง" in str(exc.value)
        db_session.expire_all()
        assert db_session.query(FinPettyBatch).count() == 1
        assert a.status == "APPROVED" and a.batch_id is None

    def test_other_integrity_errors_are_not_swallowed(self, db_session, fund, approved, monkeypatch):
        from sqlalchemy.exc import IntegrityError
        a = approved()

        def other_constraint(*args, **kwargs):
            raise IntegrityError("INSERT ...", {}, Exception("CHECK constraint failed: ck_fin_petty_claims_status"))

        monkeypatch.setattr(repo, "set_status", other_constraint)
        with pytest.raises(IntegrityError):
            _round(db_session, fund, [a])

    class _FakeDb:
        def __init__(self, dialect):
            from types import SimpleNamespace
            self._bind = SimpleNamespace(dialect=SimpleNamespace(name=dialect))
            self.calls = []

        def get_bind(self):
            return self._bind

        def execute(self, stmt, params=None):
            self.calls.append((str(stmt), params))

    def test_postgres_locks_the_round_numbers_before_reading_the_highest(self):
        db = self._FakeDb("postgresql")
        repo._lock_round_numbers(db, "LB-B02-2610-B")
        [(sql, params)] = db.calls
        assert "pg_advisory_xact_lock(hashtext(" in sql
        assert params == {"key": "fin_petty_batches:LB-B02-2610-B"}

    def test_other_databases_skip_the_lock(self):
        db = self._FakeDb("sqlite")
        repo._lock_round_numbers(db, "LB-B02-2610-B")
        assert db.calls == []
