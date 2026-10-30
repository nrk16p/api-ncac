"""ขั้นหัวหน้าอนุมัติก่อนโอน (flow v3 ผู้ใช้สั่ง 30/10/2026) — ระดับ route เรียกฟังก์ชันตรง

ผู้ขอเบิก 55555 (ระดับ 1 แผนก 30) · หัวหน้า 600050 (ระดับ 3 แผนกเดียวกัน) · เจ้าของเงินสด 12345
"""
import pytest
from fastapi import HTTPException

from models.finance_petty_model import FinPettyLog
from routes.finance import petty_routes as R
from schemas.finance_petty_schema import ApproveIn, ClaimIn, HeadApproveIn, ReturnIn
from services.finance import petty_logic as L
from services.finance import petty_mail
from services.finance import petty_repo as repo

from tests.finance.test_petty_routes import FULL, WEIGH_SPEC  # noqa: F401  (ใช้สเปกค่าชั่งชุดเดียวกัน)


def _person(eid, uid, level, dept=30, division=7, site=3, active=True):
    return {"employee_id": eid, "id": uid, "level": level, "department_id": dept,
            "division_id": division, "site_id": site, "active": active}


def _line(monkeypatch, holder_dept=99):
    """holder_dept=30 = เจ้าของเงินสดอยู่แผนกเดียวกับผู้ขอเบิก และระดับสูงพอเป็นหัวหน้าได้"""
    people = {p["employee_id"]: p for p in (
        _person("55555", 10, 1), _person("600050", 11, 3), _person("600090", 12, 5),
        _person("12345", 13, 4, dept=holder_dept, division=7 if holder_dept == 30 else 99),
        _person("700000", 14, 2),
    )}
    monkeypatch.setattr(repo, "line_context", lambda db, fresh=True: (people, {}))
    return people


@pytest.fixture
def weigh_type(db_session):
    row = repo.get_expense_type(db_session, "WEIGH")
    row.spec = WEIGH_SPEC
    db_session.commit()
    return row


@pytest.fixture
def mails(monkeypatch):
    sent = []
    monkeypatch.setattr(petty_mail.mail, "send_finance_email", lambda to, subject, html: sent.append((to, subject)))
    monkeypatch.setattr(repo, "employee_email", lambda db, eid: f"{eid}@x" if eid else None)
    return sent


@pytest.fixture
def claim(db_session, fund, weigh_type, claim_payload, weigh_items, seq_stub, frozen_bkk):
    return repo.create_claim(db_session, claim_payload(), weigh_items, actor="55555")


def _actions(db_session, claim):
    return [r.action for r in db_session.query(FinPettyLog).filter(FinPettyLog.claim_id == claim.id)
            .order_by(FinPettyLog.id).all()]


def _owner_approve(db_session, claim, eid="12345"):
    return R.approve_claim(claim.form_id, ApproveIn(employee_id=eid, attachments=FULL,
                                                    dup_override_reason="ชั่ง 2 เที่ยว"), db_session)


class TestNewClaimWaitsForTheHead:
    def test_a_new_claim_goes_to_the_head_first(self, claim):
        assert claim.status == "PENDING_HEAD"

    def test_heads_get_an_email_when_the_claim_is_opened(self, db_session, fund, weigh_type, claim_payload,
                                                         weigh_items, seq_stub, frozen_bkk, monkeypatch, mails):
        _line(monkeypatch)
        body = ClaimIn(**{**claim_payload(), "expense_date": "2026-09-25", "items": weigh_items})
        out = R.create_claim(body, db_session)
        assert out["status"] == "PENDING_HEAD"
        assert mails and mails[0][0] == ["600050@x"] and "รอหัวหน้าอนุมัติ" in mails[0][1]


class TestHeadApprove:
    def test_the_head_moves_it_to_the_cash_owner(self, db_session, claim, monkeypatch, mails):
        _line(monkeypatch)
        out = R.head_approve_claim(claim.form_id, HeadApproveIn(employee_id="600050"), db_session)
        assert out["status"] == "PENDING_APPROVAL"
        assert out["head_approved_by"] == "600050" and out["head_approved_at"]
        assert _actions(db_session, claim)[-1] == "HEAD_APPROVE"
        assert mails and mails[-1][0] == ["12345@x"]

    def test_a_higher_manager_in_the_line_may_approve_too(self, db_session, claim, monkeypatch, mails):
        _line(monkeypatch)
        out = R.head_approve_claim(claim.form_id, HeadApproveIn(employee_id="600090"), db_session)
        assert out["status"] == "PENDING_APPROVAL"

    @pytest.mark.parametrize("who", ["55555", "700000", "12345"])
    def test_requester_juniors_and_other_departments_cannot(self, db_session, claim, monkeypatch, mails, who):
        _line(monkeypatch)
        with pytest.raises(HTTPException) as exc:
            R.head_approve_claim(claim.form_id, HeadApproveIn(employee_id=who), db_session)
        assert exc.value.status_code == 403
        assert repo.get_claim(db_session, claim.form_id).status == "PENDING_HEAD"

    def test_cannot_approve_twice(self, db_session, claim, monkeypatch, mails):
        _line(monkeypatch)
        R.head_approve_claim(claim.form_id, HeadApproveIn(employee_id="600050"), db_session)
        with pytest.raises(HTTPException) as exc:
            R.head_approve_claim(claim.form_id, HeadApproveIn(employee_id="600050"), db_session)
        assert exc.value.status_code == 409


class TestCashOwnerWaitsForTheHead:
    def test_owner_cannot_pay_before_the_head(self, db_session, claim, monkeypatch, mails):
        _line(monkeypatch)
        with pytest.raises(HTTPException) as exc:
            _owner_approve(db_session, claim)
        assert exc.value.status_code == 409 and "หัวหน้า" in str(exc.value.detail)
        assert repo.get_claim(db_session, claim.form_id).status == "PENDING_HEAD"

    def test_owner_pays_after_the_head(self, db_session, claim, monkeypatch, mails):
        _line(monkeypatch)
        R.head_approve_claim(claim.form_id, HeadApproveIn(employee_id="600050"), db_session)
        out = _owner_approve(db_session, claim)
        assert out["status"] == "APPROVED" and out["head_approved_by"] == "600050"

    def test_owner_who_is_also_the_head_does_both_in_one_click(self, db_session, claim, monkeypatch, mails):
        _line(monkeypatch, holder_dept=30)
        out = _owner_approve(db_session, claim)
        assert out["status"] == "APPROVED" and out["head_approved_by"] == "12345"
        assert _actions(db_session, claim)[-2:] == ["HEAD_APPROVE", "APPROVE"]

    def test_one_click_still_respects_the_document_gate(self, db_session, claim, monkeypatch, mails):
        _line(monkeypatch, holder_dept=30)
        with pytest.raises(HTTPException) as exc:
            R.approve_claim(claim.form_id, ApproveIn(employee_id="12345", attachments={"transfer": 1}), db_session)
        assert exc.value.status_code == 403
        assert repo.get_claim(db_session, claim.form_id).status == "PENDING_HEAD"


class TestHeadReturn:
    def test_the_head_sends_it_back_with_a_reason(self, db_session, claim, monkeypatch, mails):
        _line(monkeypatch)
        out = R.return_claim(claim.form_id, ReturnIn(employee_id="600050", reason="ยอดไม่ตรงบิล"), db_session)
        assert out["status"] == "RETURNED" and out["return_reason"] == "ยอดไม่ตรงบิล"
        assert _actions(db_session, claim)[-1] == "HEAD_RETURN"
        assert mails[-1][0] == ["55555@x"]

    def test_the_cash_owner_cannot_return_while_it_waits_for_the_head(self, db_session, claim, monkeypatch, mails):
        _line(monkeypatch)
        with pytest.raises(HTTPException) as exc:
            R.return_claim(claim.form_id, ReturnIn(employee_id="12345", reason="x"), db_session)
        assert exc.value.status_code == 403

    def test_resubmitting_goes_back_to_the_head_and_clears_the_old_approval(
            self, db_session, claim, claim_payload, weigh_items, monkeypatch, mails):
        _line(monkeypatch)
        R.head_approve_claim(claim.form_id, HeadApproveIn(employee_id="600050"), db_session)
        R.return_claim(claim.form_id, ReturnIn(employee_id="12345", reason="แนบสลิปผิด"), db_session)
        body = ClaimIn(**{**claim_payload(), "expense_date": "2026-09-25", "items": weigh_items})
        out = R.update_claim(claim.form_id, body, db_session)
        assert out["status"] == "PENDING_HEAD" and out["head_approved_by"] is None
        assert any(to == ["600050@x"] for to, _ in mails[-1:])

    def test_editing_after_the_head_approved_needs_the_head_again(
            self, db_session, claim, claim_payload, weigh_items, monkeypatch, mails):
        _line(monkeypatch)
        R.head_approve_claim(claim.form_id, HeadApproveIn(employee_id="600050"), db_session)
        body = ClaimIn(**{**claim_payload(), "expense_date": "2026-09-25", "items": weigh_items[:3]})
        out = R.update_claim(claim.form_id, body, db_session)
        assert out["status"] == "PENDING_HEAD" and out["head_approved_by"] is None


class TestHeadQueue:
    def test_queue_lists_only_claims_this_head_may_approve(self, db_session, claim, monkeypatch, mails):
        _line(monkeypatch)
        mine = R.list_claims(head_approver="600050", limit=50, offset=0, db=db_session)
        assert [c["form_id"] for c in mine["items"]] == [claim.form_id]
        assert mine["items"][0]["is_primary"] is True and mine["total"] == 1
        upper = R.list_claims(head_approver="600090", limit=50, offset=0, db=db_session)
        assert upper["items"][0]["is_primary"] is False  # กดแทนได้ แต่ไม่ได้อีเมล
        assert R.list_claims(head_approver="700000", limit=50, offset=0, db=db_session)["items"] == []

    def test_history_lists_what_the_head_already_approved(self, db_session, claim, monkeypatch, mails):
        _line(monkeypatch)
        R.head_approve_claim(claim.form_id, HeadApproveIn(employee_id="600050"), db_session)
        done = R.list_claims(head_approved_by="600050", limit=50, offset=0, db=db_session)
        assert [c["form_id"] for c in done["items"]] == [claim.form_id]
        assert done["items"][0]["head_approved_by_name"] is None or isinstance(done["items"][0]["head_approved_by_name"], str)
