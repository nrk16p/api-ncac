# -*- coding: utf-8 -*-
"""ทดสอบทั้งระบบเบิก Advance แบบ dry run — เดินทั้งเส้นทางด้วยกฎจริง ไม่แตะฐานข้อมูล ไม่ส่งอีเมล

ต่างจากเทสอื่นตรงที่ไฟล์นี้ "เดินใบหนึ่งใบตั้งแต่ยื่นจนปิด" แล้วตรวจทุกอย่างที่เกิดระหว่างทาง
สถานะ · สิทธิ์ · ยอดเงิน · ใครได้อีเมล — ชั้นวงเงินใช้ชุดเดียวกับ prod
"""
from datetime import date
from decimal import Decimal

import pytest

from services.finance import advance_logic as L
from services.finance import approval_logic as rules
from services.finance import clear_approval_logic as clr
from services.finance import permission_logic as perms
from services.finance.advance_logic import AdvanceRuleError, NotAllowed, InvalidTransition

TIERS = [
    {"clause": "6.7", "amount_max": Decimal("2000"), "min_level": 2, "approver_label": "Asst. Sup", "sort_order": 1},
    {"clause": "6.6", "amount_max": Decimal("5000"), "min_level": 3, "approver_label": "Sup / Asst.M", "sort_order": 2},
    {"clause": "6.5", "amount_max": Decimal("20000"), "min_level": 5, "approver_label": "MGR / SM / DPCL", "sort_order": 3},
    {"clause": "6.4", "amount_max": Decimal("100000"), "min_level": 8, "approver_label": "CL", "sort_order": 4},
    {"clause": "6.3", "amount_max": Decimal("500000"), "min_level": 9, "approver_label": "DCEO", "sort_order": 5},
]
ACC_DEPT, OPS_DEPT = 6, 11          # 6 = แผนกบัญชี, 11 = แผนกอื่น
TODAY = date(2026, 10, 19)


def person(eid, uid, level, dept=OPS_DEPT, active=True):
    return {"employee_id": eid, "id": uid, "level": level, "department_id": dept, "active": active}


STAFF = person("STAFF", 1, 1)
LEAD = person("LEAD", 2, 3)
MGR = person("MGR", 3, 5)
CL = person("CL", 4, 8)
DCEO = person("DCEO", 5, 9, dept=99)
ACCOUNTANT = person("ACC", 6, 3, dept=ACC_DEPT)
ORG = {p["employee_id"]: p for p in (STAFF, LEAD, MGR, CL, DCEO, ACCOUNTANT)}
NO_MAP = {}

class Form:
    """ใบคำขอหนึ่งใบในหน่วยความจำ — เลียนแบบสองคอลัมน์จริง status_approve + fin_status"""

    def __init__(self, requester, amount):
        self.requester = requester
        self.amount = Decimal(str(amount))
        self.status_approve = "In Progress"
        self.fin_status = None
        self.level = 1
        self.logs = []
        self.clear_due_date = None
        self.amount_paid = None
        self.amount_actual = None
        self.settle_amount = None
        self.clear_level = None
        self.clear_clause = None
        self.clear_logs = []
        self.plan = rules.evaluate(TIERS, ORG, NO_MAP, requester["employee_id"], self.amount)

    @property
    def status(self):
        return L.derive_status(self.status_approve, self.fin_status, self.clear_due_date, TODAY)[0]

    def approve(self, approver_id):
        d = rules.decide(self.plan, ORG, NO_MAP, self.requester["employee_id"], approver_id,
                         self.level, rules.current_round(self.logs))
        if not d["allowed"]:
            raise NotAllowed(f"{approver_id} อนุมัติขั้น {d['step']} ไม่ได้")
        self.logs.append({"id": len(self.logs) + 1, "level_no": d["step"], "action": rules.ACTION_APPROVED,
                          "action_by": ORG[approver_id]["id"], "action_at": None, "remark": None})
        if d["outcome"] == rules.OUTCOME_APPROVED:
            self.status_approve = "Approved"
        else:
            self.level = d["next_step"]
        return d

    def mail_to(self):
        """อีเมลขั้นปัจจุบันจะถึงใครบ้าง (เฉพาะระดับที่ TOA ต้องการ)"""
        state = rules.evaluate_step(self.plan, ORG, NO_MAP, self.requester["employee_id"], self.level,
                                    rules.current_round(self.logs))
        el = rules.eligible_approvers(ORG.values(), self.requester, state["required_level"], NO_MAP,
                                      state["excluded"])
        return sorted(p["employee_id"] for p in rules.notify_approvers(el, state["required_level"]))


# ======================= เส้นทางหลัก =======================

def test_happy_path_small_amount_from_staff():
    """พนักงาน lv1 เบิก 3,000 · ขั้น lv2 → lv3 ตาม TOA · เคลียร์ใช้ไม่หมด · คืนเงิน · ปิดรายการ"""
    f = Form(STAFF, 3000)
    assert f.status == L.PENDING_APPROVAL
    assert [s["required_level"] for s in f.plan["steps"]] == [2, 3]
    assert f.mail_to() == ["LEAD"], "แผนกไม่มี lv2 อีเมลขั้น 1 จึงถึงหัวหน้า lv3"

    f.approve("LEAD")                                     # lv3 ถึงขั้น 2 ด้วย → ข้ามขั้น จบในคลิกเดียว
    assert f.status_approve == "Approved"
    assert f.status == L.AWAITING_VOUCHER

    L.check_voucher(f.status, voucher_date=TODAY)
    f.fin_status = L.FIN_VOUCHERED
    assert f.status == L.AWAITING_PAYMENT

    L.check_pay(f.status, acc_active=True, amount_paid=3000, transfer_date=TODAY, clear_due_date=TODAY)
    f.fin_status, f.amount_paid = L.FIN_PAID, Decimal("3000")
    f.clear_due_date = date(2026, 10, 26)
    assert f.status == L.AWAITING_CLEARING

    rows, total = L.check_clear_items([{"expense_date": TODAY, "description": "ค่าเดินทาง",
                                        "amount_before_vat": 2500}])
    settle, settle_date = L.check_clear(f.status, is_owner=True, amount_paid=f.amount_paid,
                                        clear_date=TODAY, amount_actual=total, settle_date=TODAY)
    assert (total, settle) == (Decimal("2500"), Decimal("500")), "ใช้ 2,500 จาก 3,000 ต้องคืน 500"
    f.amount_actual, f.settle_amount = total, settle

    info = clr.plan(TIERS, ORG, NO_MAP, f.requester["employee_id"], f.amount)   # ขั้นตามใบขอ (26/10/2026)
    assert clr.needs_approval(info) is True
    f.fin_status, f.clear_level, f.clear_clause = L.FIN_CLEAR_PENDING, clr.STEP_LEADER, info["clause"]
    assert f.status == L.CLEARING_APPROVAL

    with pytest.raises(AdvanceRuleError):
        L.check_send_back(f.status, review_remark="บัญชียังตรวจไม่ได้")   # ต้องรอหัวหน้าก่อน

    f.fin_status = L.FIN_CLEARING_SUBMITTED
    assert f.status == L.AWAITING_REVIEW
    assert L.check_confirm(f.status, settle_amount=f.settle_amount, settle_date=TODAY) is None
    f.fin_status = L.FIN_CLOSED
    assert f.status == L.CLOSED


def test_15000_goes_to_the_lead_then_the_manager():
    """ไล่ทุกขั้นตามชั้น TOA (ผู้ใช้สั่ง 26/10/2026): ข้อ 6.5 → lv2 → lv3 → lv5"""
    f = Form(STAFF, 15000)
    assert [s["required_level"] for s in f.plan["steps"]] == [2, 3, 5]
    assert f.mail_to() == ["LEAD"]
    f.approve("LEAD")                                      # หัวหน้า lv3 ผ่านขั้น 1 และข้ามขั้น 2 (lv3) ได้เลย
    assert f.status_approve == "In Progress" and f.level == 3
    assert f.mail_to() == ["MGR"]
    with pytest.raises(NotAllowed):
        f.approve("LEAD")                                  # คนเดิมกดสองขั้นไม่ได้
    f.approve("MGR")
    assert f.status_approve == "Approved"


def test_the_manager_may_sign_step_1_alone():
    f = Form(STAFF, 15000)
    d = f.approve("MGR")                                   # lv5 ถึงขั้น 2 และ 3 → ข้ามขั้นได้
    assert d["skipped"] == [2, 3] and f.status_approve == "Approved"


def test_higher_level_may_approve_through_the_link():
    f = Form(STAFF, 3000)
    f.approve("CL")                                        # lv8 ไม่ได้อีเมล แต่เปิดลิงก์กดได้
    assert f.status_approve == "Approved"


def test_level_8_requester_keeps_a_single_step():
    f = Form(CL, 150000)
    assert len(f.plan["steps"]) == 1
    assert f.mail_to() == ["DCEO"]
    f.approve("DCEO")
    assert f.status_approve == "Approved"


# ======================= เส้นทางย้อนกลับ =======================

def test_rejected_form_stops_there():
    f = Form(STAFF, 3000)
    f.status_approve = "Rejected"
    assert f.status == L.REJECTED
    for call in (lambda: L.check_voucher(f.status, voucher_date=TODAY),
                 lambda: L.check_pay(f.status, acc_active=True, amount_paid=1, transfer_date=TODAY,
                                     clear_due_date=TODAY)):
        with pytest.raises(AdvanceRuleError):
            call()


def test_accounting_returns_the_form_and_the_requester_resubmits():
    f = Form(STAFF, 15000)
    f.approve("MGR")
    L.check_return(f.status, remark="ใส่ศูนย์ค่าใช้จ่ายผิด")
    f.fin_status = L.FIN_RETURNED              # ยังเป็น Approved อยู่ สถานะจึงเป็น "ตีกลับให้แก้ไข"
    assert f.status == L.RETURNED
    L.check_resubmit(f.status, is_owner=True)
    f.status_approve, f.fin_status = "In Progress", L.FIN_RESUBMITTED
    assert f.status == L.PENDING_APPROVAL
    f.logs += [{"id": 90, "level_no": 0, "action": rules.MARKER_RETURNED, "action_by": None, "action_at": None},
               {"id": 91, "level_no": 0, "action": rules.MARKER_RESUBMITTED, "action_by": None, "action_at": None}]
    f.level = 1
    assert f.mail_to() == ["LEAD"], "รอบใหม่ เริ่มขั้น 1 ใหม่ หัวหน้ากลับมาอยู่ในคิว"
    f.approve("MGR")
    assert f.status_approve == "Approved"


def test_return_without_a_reason_is_refused():
    with pytest.raises(AdvanceRuleError):
        L.check_return(L.AWAITING_VOUCHER, remark="   ")


def test_clearing_rejected_by_the_leader_goes_back_to_the_requester():
    f = Form(STAFF, 15000)
    f.approve("MGR")
    f.fin_status, f.amount_paid = L.FIN_PAID, Decimal("15000")
    rows, total = L.check_clear_items([{"expense_date": TODAY, "description": "ค่าที่พัก",
                                        "amount_before_vat": 9000}])
    L.check_clear(f.status, is_owner=True, amount_paid=f.amount_paid, clear_date=TODAY,
                  amount_actual=total, settle_date=TODAY)
    info = clr.plan(TIERS, ORG, NO_MAP, "STAFF", f.amount)
    f.fin_status, f.clear_clause = L.FIN_CLEAR_PENDING, info["clause"]
    with pytest.raises(AdvanceRuleError):
        clr.check_reject(True, True, "  ")                 # ไม่อนุมัติต้องมีเหตุผล
    clr.check_reject(True, True, "ใบเสร็จไม่ครบ")
    f.fin_status = L.FIN_SENT_BACK
    assert f.status == L.SENT_BACK
    # แก้แล้วส่งใหม่ ขั้นยังตามใบขอ ชั้นไม่เปลี่ยน ไม่ต้องขออนุมัติซ้ำ
    rows, total2 = L.check_clear_items([{"expense_date": TODAY, "description": "ค่าที่พัก",
                                         "amount_before_vat": 9500}])
    new_clause = clr.clause_for(TIERS, f.amount)
    assert clr.needs_reapproval(f.clear_clause, new_clause) is False


def test_clearing_that_crosses_a_tier_must_be_approved_again():
    assert clr.needs_reapproval("6.5", "6.6") is True
    assert clr.needs_reapproval(None, "6.6") is True       # ส่งเคลียร์ครั้งแรกเสมอ


# ======================= ยอดเงิน =======================

@pytest.mark.parametrize("paid,actual,settle", [
    (10000, 7000, Decimal("3000")),        # เหลือ คืนบริษัท
    (10000, 10000, Decimal("0")),          # พอดี
    (10000, 12000, Decimal("-2000")),      # ใช้เกิน บริษัทจ่ายเพิ่ม
])
def test_settlement_amount(paid, actual, settle):
    assert L.compute_settle_amount(Decimal(paid), Decimal(actual)) == settle


def test_money_owed_to_the_company_needs_a_transfer_date():
    with pytest.raises(AdvanceRuleError):
        L.check_clear(L.AWAITING_CLEARING, is_owner=True, amount_paid=Decimal("10000"),
                      clear_date=TODAY, amount_actual=Decimal("7000"), settle_date=None)


def test_money_owed_to_the_employee_needs_a_transfer_date_at_close():
    with pytest.raises(AdvanceRuleError):
        L.check_confirm(L.AWAITING_REVIEW, settle_amount=Decimal("-2000"), settle_date=None)
    assert L.check_confirm(L.AWAITING_REVIEW, settle_amount=Decimal("-2000"), settle_date=TODAY) == TODAY


def test_only_the_requester_can_submit_the_clearing():
    with pytest.raises(NotAllowed):
        L.check_clear(L.AWAITING_CLEARING, is_owner=False, amount_paid=Decimal("1"), clear_date=TODAY,
                      amount_actual=Decimal("1"), settle_date=None)


def test_clearing_rows_must_carry_an_amount():
    with pytest.raises(AdvanceRuleError):
        L.check_clear_items([])


# ======================= สิทธิ์ตามหน้าที่ =======================

@pytest.mark.parametrize("permission", [
    perms.PERM_VOUCHER, perms.PERM_PAY, perms.PERM_RETURN, perms.PERM_CLEARING_REVIEW,
    perms.PERM_CLOSE, perms.PERM_PAYEE_ADMIN, perms.PERM_OVERDUE_REMIND,
])
def test_each_permission_belongs_to_one_department(permission):
    """จ่ายเงิน + บัญชีรับเงิน = การเงิน (4) · ปุ่มอื่น = บัญชี (6) · แผนกอื่นไม่ได้สักปุ่ม (ผู้ใช้สั่ง 21/10/2026)"""
    owner, other = (4, 6) if permission in (perms.PERM_PAY, perms.PERM_PAYEE_ADMIN) else (6, 4)
    assert perms.allowed(permission, "STAFF", owner) is True
    assert perms.allowed(permission, "STAFF", other) is False
    assert perms.allowed(permission, "STAFF", 11) is False


def test_permissions_for_lists_what_a_person_may_do():
    got = perms.permissions_for("ACC", 6)
    assert perms.PERM_VOUCHER in got and perms.PERM_PAY not in got
    assert perms.permissions_for("FIN", 4) == [perms.PERM_PAY, perms.PERM_PAYEE_ADMIN]


# ======================= แจ้งเตือน =======================

def test_executives_are_never_emailed_for_a_small_form_but_may_still_approve():
    f = Form(STAFF, 3000)
    assert "DCEO" not in f.mail_to()
    f.approve("DCEO")                                      # ระดับ 9 ข้ามแผนกและข้ามขั้นได้
    assert f.status_approve == "Approved"


# ======================= เกินกำหนดเคลียร์ =======================

def test_overdue_only_while_waiting_for_the_clearing():
    due = date(2026, 10, 10)
    assert L.derive_status("Approved", L.FIN_PAID, due, TODAY) == (L.AWAITING_CLEARING, True)
    assert L.derive_status("Approved", L.FIN_SENT_BACK, due, TODAY) == (L.SENT_BACK, True)
    assert L.derive_status("Approved", L.FIN_CLEAR_PENDING, due, TODAY)[1] is False
    assert L.derive_status("Approved", L.FIN_CLOSED, due, TODAY) == (L.CLOSED, False)


def test_every_status_has_a_thai_label():
    for status in L.ALL_STATUSES:
        assert L.STATUS_LABELS[status].strip()
        assert not L.STATUS_LABELS[status].isascii(), f"{status} ยังเป็นภาษาอังกฤษ"
