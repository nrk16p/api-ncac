from datetime import date
from decimal import Decimal

import pytest

from services.finance import petty_repo as repo


class TestCreateClaim:
    def test_amount_is_computed_from_items(self, db_session, fund, claim_payload, weigh_items, seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        assert claim.amount == Decimal("1640.00")

    def test_period_comes_from_items(self, db_session, fund, claim_payload, weigh_items, seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        assert (claim.period_from, claim.period_to) == (date(2026, 9, 17), date(2026, 9, 24))

    def test_starts_waiting_for_the_head(self, db_session, fund, claim_payload, weigh_items, seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        assert claim.status == "PENDING_HEAD"  # flow v3: หัวหน้าอนุมัติก่อนโอน

    def test_form_id_shape(self, db_session, fund, claim_payload, weigh_items, seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        assert claim.form_id == "LB-B02-2610-001"

    def test_second_claim_gets_the_next_number(self, db_session, fund, claim_payload, weigh_items, seq_stub, frozen_bkk):
        repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        second = repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        assert second.form_id == "LB-B02-2610-002"

    def test_items_are_numbered_and_priced(self, db_session, fund, claim_payload, weigh_items, seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        items = repo.claim_items(db_session, claim.id)
        assert len(items) == 16
        assert [i.line_no for i in items] == list(range(1, 17))
        assert items[0].amount == Decimal("100.00")

    def test_duplicate_truck_in_one_claim_is_stored(self, db_session, fund, claim_payload, weigh_items, seq_stub, frozen_bkk):
        # ของจริงมี 1093 สองแถวในใบเดียว — ต้องเก็บได้ ไม่ใช่ถูกยุบ
        items = [dict(weigh_items[0], truck_code="1093", ref_no="000120"),
                 dict(weigh_items[0], truck_code="1093", ref_no="000121")]
        claim = repo.create_claim(db_session, claim_payload(), items, actor="55555")
        assert len(repo.claim_items(db_session, claim.id)) == 2

    def test_creates_a_log_row(self, db_session, fund, claim_payload, weigh_items, seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        from models.finance_petty_model import FinPettyLog
        logs = db_session.query(FinPettyLog).filter(FinPettyLog.claim_id == claim.id).all()
        assert [l.action for l in logs] == ["CREATE"]

    def test_unknown_fund_is_rejected(self, db_session, fund, claim_payload, weigh_items, seq_stub, frozen_bkk):
        with pytest.raises(LookupError):
            repo.create_claim(db_session, claim_payload(fund_id=fund.id + 99), weigh_items,
                              actor="55555")

    def test_inactive_fund_is_rejected(self, db_session, fund, claim_payload, weigh_items, seq_stub, frozen_bkk):
        fund.is_active = False
        db_session.commit()
        with pytest.raises(LookupError):
            repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")


class TestReplaceItems:
    def test_replacing_items_updates_total(self, db_session, fund, claim_payload, weigh_items, seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        total = repo.replace_items(db_session, claim.id, weigh_items[:1])
        assert total == Decimal("100.00")
        assert repo.get_claim(db_session, claim.form_id).amount == Decimal("100.00")

    def test_old_rows_are_gone(self, db_session, fund, claim_payload, weigh_items, seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        repo.replace_items(db_session, claim.id, weigh_items[:2])
        assert len(repo.claim_items(db_session, claim.id)) == 2


class TestListClaims:
    def test_filters_by_creator(self, db_session, fund, claim_payload, weigh_items, seq_stub, frozen_bkk):
        repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        repo.create_claim(db_session, claim_payload(created_by='77777'), weigh_items, actor="77777")
        rows, total = repo.list_claims(db_session, fund_ids=None, created_by="55555", status=None,
                                       month=None, limit=50, offset=0)
        assert total == 1 and rows[0].created_by == "55555"

    def test_filters_by_fund(self, db_session, fund, claim_payload, weigh_items, seq_stub, frozen_bkk):
        repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        _, total = repo.list_claims(db_session, fund_ids=[fund.id + 99], created_by=None, status=None,
                                    month=None, limit=50, offset=0)
        assert total == 0

    def test_empty_fund_list_matches_nothing(self, db_session, fund, claim_payload, weigh_items, seq_stub, frozen_bkk):
        repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        _, total = repo.list_claims(db_session, fund_ids=[], created_by=None, status=None,
                                    month=None, limit=50, offset=0)
        assert total == 0

    def test_filters_by_month_on_expense_date(self, db_session, fund, claim_payload, weigh_items, seq_stub, frozen_bkk):
        repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")
        _, in_month = repo.list_claims(db_session, fund_ids=None, created_by=None, status=None,
                                       month="2026-09", limit=50, offset=0)
        _, other = repo.list_claims(db_session, fund_ids=None, created_by=None, status=None,
                                    month="2026-08", limit=50, offset=0)
        assert (in_month, other) == (1, 0)

    def test_december_month_filter_does_not_overflow(self, db_session, fund, claim_payload, weigh_items, seq_stub, frozen_bkk):
        repo.create_claim(db_session, claim_payload(expense_date=date(2026, 12, 31)),
                          weigh_items, actor="55555")
        _, total = repo.list_claims(db_session, fund_ids=None, created_by=None, status=None,
                                    month="2026-12", limit=50, offset=0)
        assert total == 1

    def test_paging_returns_full_count(self, db_session, fund, claim_payload, weigh_items, seq_stub, frozen_bkk):
        for _ in range(3):
            repo.create_claim(db_session, claim_payload(), weigh_items[:1], actor="55555")
        rows, total = repo.list_claims(db_session, fund_ids=None, created_by=None, status=None,
                                       month=None, limit=2, offset=0)
        assert (len(rows), total) == (2, 3)
