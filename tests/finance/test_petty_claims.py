from datetime import date
from decimal import Decimal

import pytest

from models.finance_petty_model import FinPettyExpenseType, FinPettyFund
from services.finance import petty_repo as repo


@pytest.fixture
def fund(db_session):
    f = FinPettyFund(branch_code="03", division_code="B02", holder_employee_id="12345")
    db_session.add(FinPettyExpenseType(code="WEIGH", name_th="ค่าชั่งน้ำหนัก", spec={"item_based": True}))
    db_session.add(f)
    db_session.commit()
    return f


WEIGH_ITEMS = (
    [{"event_date": date(2026, 9, 17), "truck_code": "161", "truck_plate": "71-4285",
      "truck_type": "โม่ใหญ่", "ref_no": "000055", "qty": 1, "rate": 100}] * 12
    + [{"event_date": date(2026, 9, 20), "truck_code": "153", "truck_plate": "71-4728",
        "truck_type": "โม่ใหญ่", "ref_no": "000101", "qty": 1, "rate": 120}] * 3
    + [{"event_date": date(2026, 9, 24), "truck_code": "165", "truck_plate": "71-9001",
        "truck_type": "โม่เล็ก", "ref_no": "000172", "qty": 1, "rate": 80}]
)


def _payload(fund):
    return {"fund_id": fund.id, "expense_type": "WEIGH", "expense_date": date(2026, 9, 25),
            "payee_name": "อัญชัญ น่วมมี", "created_by": "55555"}


class TestCreateClaim:
    def test_amount_is_computed_from_items(self, db_session, fund, seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, _payload(fund), WEIGH_ITEMS, actor="55555")
        assert claim.amount == Decimal("1640.00")

    def test_period_comes_from_items(self, db_session, fund, seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, _payload(fund), WEIGH_ITEMS, actor="55555")
        assert (claim.period_from, claim.period_to) == (date(2026, 9, 17), date(2026, 9, 24))

    def test_starts_pending_approval(self, db_session, fund, seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, _payload(fund), WEIGH_ITEMS, actor="55555")
        assert claim.status == "PENDING_APPROVAL"

    def test_form_id_shape(self, db_session, fund, seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, _payload(fund), WEIGH_ITEMS, actor="55555")
        assert claim.form_id == "03-B02-2610-001"

    def test_second_claim_gets_the_next_number(self, db_session, fund, seq_stub, frozen_bkk):
        repo.create_claim(db_session, _payload(fund), WEIGH_ITEMS, actor="55555")
        second = repo.create_claim(db_session, _payload(fund), WEIGH_ITEMS, actor="55555")
        assert second.form_id == "03-B02-2610-002"

    def test_items_are_numbered_and_priced(self, db_session, fund, seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, _payload(fund), WEIGH_ITEMS, actor="55555")
        items = repo.claim_items(db_session, claim.id)
        assert len(items) == 16
        assert [i.line_no for i in items] == list(range(1, 17))
        assert items[0].amount == Decimal("100.00")

    def test_duplicate_truck_in_one_claim_is_stored(self, db_session, fund, seq_stub, frozen_bkk):
        # ของจริงมี 1093 สองแถวในใบเดียว — ต้องเก็บได้ ไม่ใช่ถูกยุบ
        items = [dict(WEIGH_ITEMS[0], truck_code="1093", ref_no="000120"),
                 dict(WEIGH_ITEMS[0], truck_code="1093", ref_no="000121")]
        claim = repo.create_claim(db_session, _payload(fund), items, actor="55555")
        assert len(repo.claim_items(db_session, claim.id)) == 2

    def test_creates_a_log_row(self, db_session, fund, seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, _payload(fund), WEIGH_ITEMS, actor="55555")
        from models.finance_petty_model import FinPettyLog
        logs = db_session.query(FinPettyLog).filter(FinPettyLog.claim_id == claim.id).all()
        assert [l.action for l in logs] == ["CREATE"]

    def test_unknown_fund_is_rejected(self, db_session, fund, seq_stub, frozen_bkk):
        with pytest.raises(LookupError):
            repo.create_claim(db_session, dict(_payload(fund), fund_id=fund.id + 99), WEIGH_ITEMS,
                              actor="55555")

    def test_inactive_fund_is_rejected(self, db_session, fund, seq_stub, frozen_bkk):
        fund.is_active = False
        db_session.commit()
        with pytest.raises(LookupError):
            repo.create_claim(db_session, _payload(fund), WEIGH_ITEMS, actor="55555")


class TestReplaceItems:
    def test_replacing_items_updates_total(self, db_session, fund, seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, _payload(fund), WEIGH_ITEMS, actor="55555")
        total = repo.replace_items(db_session, claim.id, WEIGH_ITEMS[:1])
        assert total == Decimal("100.00")
        assert repo.get_claim(db_session, claim.form_id).amount == Decimal("100.00")

    def test_old_rows_are_gone(self, db_session, fund, seq_stub, frozen_bkk):
        claim = repo.create_claim(db_session, _payload(fund), WEIGH_ITEMS, actor="55555")
        repo.replace_items(db_session, claim.id, WEIGH_ITEMS[:2])
        assert len(repo.claim_items(db_session, claim.id)) == 2


class TestListClaims:
    def test_filters_by_creator(self, db_session, fund, seq_stub, frozen_bkk):
        repo.create_claim(db_session, _payload(fund), WEIGH_ITEMS, actor="55555")
        repo.create_claim(db_session, dict(_payload(fund), created_by="77777"), WEIGH_ITEMS, actor="77777")
        rows, total = repo.list_claims(db_session, fund_ids=None, created_by="55555", status=None,
                                       month=None, limit=50, offset=0)
        assert total == 1 and rows[0].created_by == "55555"

    def test_filters_by_fund(self, db_session, fund, seq_stub, frozen_bkk):
        repo.create_claim(db_session, _payload(fund), WEIGH_ITEMS, actor="55555")
        _, total = repo.list_claims(db_session, fund_ids=[fund.id + 99], created_by=None, status=None,
                                    month=None, limit=50, offset=0)
        assert total == 0

    def test_empty_fund_list_matches_nothing(self, db_session, fund, seq_stub, frozen_bkk):
        repo.create_claim(db_session, _payload(fund), WEIGH_ITEMS, actor="55555")
        _, total = repo.list_claims(db_session, fund_ids=[], created_by=None, status=None,
                                    month=None, limit=50, offset=0)
        assert total == 0

    def test_filters_by_month_on_expense_date(self, db_session, fund, seq_stub, frozen_bkk):
        repo.create_claim(db_session, _payload(fund), WEIGH_ITEMS, actor="55555")
        _, in_month = repo.list_claims(db_session, fund_ids=None, created_by=None, status=None,
                                       month="2026-09", limit=50, offset=0)
        _, other = repo.list_claims(db_session, fund_ids=None, created_by=None, status=None,
                                    month="2026-08", limit=50, offset=0)
        assert (in_month, other) == (1, 0)

    def test_december_month_filter_does_not_overflow(self, db_session, fund, seq_stub, frozen_bkk):
        repo.create_claim(db_session, dict(_payload(fund), expense_date=date(2026, 12, 31)),
                          WEIGH_ITEMS, actor="55555")
        _, total = repo.list_claims(db_session, fund_ids=None, created_by=None, status=None,
                                    month="2026-12", limit=50, offset=0)
        assert total == 1

    def test_paging_returns_full_count(self, db_session, fund, seq_stub, frozen_bkk):
        for _ in range(3):
            repo.create_claim(db_session, _payload(fund), WEIGH_ITEMS[:1], actor="55555")
        rows, total = repo.list_claims(db_session, fund_ids=None, created_by=None, status=None,
                                       month=None, limit=2, offset=0)
        assert (len(rows), total) == (2, 3)
