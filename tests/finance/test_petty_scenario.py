"""ซ้อมใช้งานจริงตามที่ผู้ใช้สั่ง 2026-10-13

master: กองทุนสาขา 03 (สลบ) ดิวิชัน B02 · ผู้ถือเงินและผู้อนุมัติคนเดียวกัน 670108 · วงเงิน 10,000
ผู้เปิดใบคือ kittaboon เบิกสามประเภท: ค่าปะยาง · ค่าชั่งน้ำหนัก · ค่าเดินทาง
และผู้อนุมัติของอีกแผนกต้องแตะใบนี้ไม่ได้

spec ที่ใช้อ่านจากไฟล์ migration จริง ไม่ใช่ของปลอมในเทสต์ — ถ้า spec เปลี่ยน เทสต์นี้จะรู้
"""
import json
import pathlib
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from models.finance_petty_model import FinPettyExpenseType, FinPettyFund
from routes.finance import petty_routes as R
from schemas.finance_petty_schema import ApproveIn, SubmitAccIn, TransferIn
from services.finance import petty_logic as L
from services.finance import petty_repo as repo

MIGRATIONS = pathlib.Path(__file__).resolve().parents[2] / "scripts/migrations"
HOLDER = "670108"          # ผู้ถือเงิน = ผู้อนุมัติ
CLERK = "kittaboon"        # ผู้เปิดใบ
OTHER_APPROVER = "880001"  # ผู้ถือเงินของอีกแผนก


def _spec(code: str) -> dict:
    name = ("2026-10-12_finance_petty_cash.sql" if code == "WEIGH"
            else "2026-10-13_finance_petty_types.sql")
    text = (MIGRATIONS / name).read_text(encoding="utf-8")
    body = text.split(f"-- spec:{code}")[1].split("-- /spec")[0].strip()
    return json.loads(body.strip().strip("'"))


def _required_attachments(code: str) -> dict[str, int]:
    return {a["key"]: 1 for a in _spec(code)["attachments"] if a.get("required")}


@pytest.fixture
def master(db_session):
    """master ที่ตั้งตามที่สั่ง: หนึ่งกองทุน หนึ่งผู้ถือเงิน วงเงิน 10,000 + กองทุนอีกแผนกไว้ลองข้ามแผนก"""
    for code, name in (("WEIGH", "ค่าชั่งน้ำหนัก"), ("TIRE", "ค่าปะ-เปลี่ยนยาง"),
                       ("TRAVEL", "ค่าเดินทาง")):
        db_session.add(FinPettyExpenseType(code=code, name_th=name, spec=_spec(code)))
    mine = FinPettyFund(branch_code="03", division_code="B02", holder_employee_id=HOLDER,
                        fund_limit=Decimal("10000.00"))
    theirs = FinPettyFund(branch_code="03", division_code="A01",
                          holder_employee_id=OTHER_APPROVER, fund_limit=Decimal("5000.00"))
    db_session.add_all([mine, theirs])
    db_session.commit()
    return {"fund": mine, "other_fund": theirs}


def _open(db_session, fund, expense_type, items, **over):
    payload = {"fund_id": fund.id, "expense_type": expense_type,
               "expense_date": date(2026, 9, 30), "payee_name": "อัญชัญ น่วมมี",
               "created_by": CLERK}
    payload.update(over)
    return repo.create_claim(db_session, payload, items, actor=CLERK)


TIRE_ITEMS = [{"event_date": date(2026, 9, 26), "truck_code": "6604", "truck_plate": "72-8179",
               "truck_type": "โม่ใหญ่", "ref_no": "LBTIRE2609001",
               "remark": "เปลี่ยนยาง 6 เส้น", "qty": 6, "rate": 200}]

TRAVEL_ITEMS = [{"event_date": date(2026, 9, 29), "ref_no": "TRIP-8891", "qty": 1, "rate": 243,
                 "remark": "คลองตัน → เมกาบางนา (ขากลับ)"}]


@pytest.fixture
def three_claims(db_session, master, weigh_items, seq_stub, frozen_bkk):
    fund = master["fund"]
    return {
        "TIRE": _open(db_session, fund, "TIRE", TIRE_ITEMS),
        "WEIGH": _open(db_session, fund, "WEIGH", weigh_items),
        "TRAVEL": _open(db_session, fund, "TRAVEL", TRAVEL_ITEMS,
                        detail={"dept": "จัดส่ง", "leg": "ขากลับ", "route_from": "คลองตัน",
                                "route_to": "เมกาบางนา", "reason": "ไปรับรถที่ศูนย์"}),
    }


class TestMaster:
    def test_the_fund_belongs_to_one_holder_with_a_ten_thousand_limit(self, master):
        fund = master["fund"]
        assert (fund.holder_employee_id, fund.fund_limit) == (HOLDER, Decimal("10000.00"))

    def test_the_three_types_came_from_the_real_specs(self, db_session, master):
        tire = repo.get_expense_type(db_session, "TIRE")
        assert tire.spec["atms"]["product_group"] == "ค่าแรง-ระบบยาง"


class TestOpeningTheThreeClaims:
    def test_totals_match_the_sample_bills(self, three_claims):
        assert [three_claims[k].amount for k in ("TIRE", "WEIGH", "TRAVEL")] == \
            [Decimal("1200.00"), Decimal("1640.00"), Decimal("243.00")]

    def test_each_claim_gets_its_own_form_id(self, three_claims):
        assert [three_claims[k].form_id for k in ("TIRE", "WEIGH", "TRAVEL")] == \
            ["LB-B02-2610-001", "LB-B02-2610-002", "LB-B02-2610-003"]

    def test_no_claim_carries_a_project_code(self, three_claims):
        assert all(not hasattr(c, "project_code") for c in three_claims.values())

    def test_kittaboon_is_recorded_as_the_clerk(self, three_claims):
        assert all(c.created_by == CLERK for c in three_claims.values())

    def test_every_claim_waits_for_the_holder(self, three_claims):
        assert {c.status for c in three_claims.values()} == {"PENDING_APPROVAL"}


class TestTheApproverCannotCrossDepartments:
    """ผู้ใช้สั่ง 2026-10-13: ผู้อนุมัติจะไม่ข้ามแผนก"""

    def test_a_holder_of_another_division_is_refused(self, db_session, three_claims):
        claim = three_claims["TIRE"]
        with pytest.raises(HTTPException) as exc:
            R.approve_claim(claim.form_id,
                            ApproveIn(employee_id=OTHER_APPROVER,
                                      attachments=_required_attachments("TIRE")), db=db_session)
        assert exc.value.status_code == 403

    def test_the_claim_is_untouched_after_the_refusal(self, db_session, three_claims):
        claim = three_claims["TIRE"]
        with pytest.raises(HTTPException):
            R.approve_claim(claim.form_id, ApproveIn(employee_id=OTHER_APPROVER), db=db_session)
        db_session.refresh(claim)
        assert (claim.status, claim.approved_by) == ("PENDING_APPROVAL", None)

    def test_the_other_division_does_not_even_see_it_in_its_own_fund(self, db_session, master,
                                                                     three_claims):
        rows = repo.list_claims(db_session, fund_ids=[master["other_fund"].id],
                                created_by=None, status=None, month=None, limit=50, offset=0)
        assert rows == ([], 0)


class TestTheDocumentGate:
    def test_tyre_cannot_be_approved_without_the_receipt(self, db_session, three_claims):
        claim = three_claims["TIRE"]
        with pytest.raises(HTTPException) as exc:
            R.approve_claim(claim.form_id,
                            ApproveIn(employee_id=HOLDER, attachments={"transfer": 1}),
                            db=db_session)
        assert exc.value.status_code == 403

    def test_travel_needs_the_approved_request_form(self, db_session, three_claims):
        claim = three_claims["TRAVEL"]
        attachments = _required_attachments("TRAVEL")
        attachments.pop("request_form")
        with pytest.raises(HTTPException) as exc:
            R.approve_claim(claim.form_id,
                            ApproveIn(employee_id=HOLDER, attachments=attachments), db=db_session)
        assert exc.value.status_code == 403

    def test_weighing_warns_once_about_truck_1093_then_goes_through(self, db_session, three_claims):
        claim = three_claims["WEIGH"]
        with pytest.raises(HTTPException) as exc:
            R.approve_claim(claim.form_id,
                            ApproveIn(employee_id=HOLDER,
                                      attachments=_required_attachments("WEIGH")), db=db_session)
        assert exc.value.status_code == 422 and "ซ้ำ" in str(exc.value.detail)


class TestTheHolderApprovesAllThree:
    @pytest.fixture
    def approved(self, db_session, three_claims):
        out = {}
        for code, claim in three_claims.items():
            extra = {"dup_override_reason": "รถ 1093 ชั่งสองเที่ยว คนละบัตร"} if code == "WEIGH" else {}
            out[code] = R.approve_claim(
                claim.form_id,
                ApproveIn(employee_id=HOLDER, attachments=_required_attachments(code), **extra),
                db=db_session)
        return out

    def test_all_three_are_approved_by_the_holder(self, approved):
        assert {(c["status"], c["approved_by"]) for c in approved.values()} == \
            {("APPROVED", HOLDER)}

    def test_the_fund_now_holds_three_thousand_and_eighty_three(self, db_session, master, approved):
        assert repo.petty_committed(db_session, HOLDER) == Decimal("3083.00")

    def test_the_response_carries_the_lettered_number_only(self, approved):
        assert approved["TRAVEL"]["form_id"] == "LB-B02-2610-003" and "project_code" not in approved["TRAVEL"]

    def test_the_whole_round_trip_reaches_accounting(self, db_session, approved):
        claim = approved["TIRE"]
        R.transfer_claim(claim["form_id"],
                         TransferIn(employee_id=HOLDER, transfer_date=date(2026, 10, 1)),
                         db=db_session)
        sent = R.submit_to_accounting(claim["form_id"],
                                   SubmitAccIn(employee_id=HOLDER, dd_no="LBDD26090931",
                                               attachments=_required_attachments("TIRE")),
                                   db=db_session)
        assert sent["status"] == "AWAITING_REVIEW"
