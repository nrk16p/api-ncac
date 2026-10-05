"""Finance — เบิกเงิน Advance. Tables are created by scripts/migrations/2026-09-28_finance_advance.sql
(and create_all, which is a no-op once they exist). Keep both in sync."""
from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB

from database import Base


class FinAccount(Base):
    __tablename__ = "fin_accounts"

    acc_code = Column(String(20), primary_key=True)
    acc_name = Column(String(255), nullable=False)
    acc_name_en = Column(String(255))
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class FinAdvance(Base):
    __tablename__ = "fin_advances"

    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey("form_submissions.id"), nullable=False, unique=True)
    form_id = Column(String(80), nullable=False, unique=True)

    # step 3 — Finance pays
    acc_code = Column(String(20), ForeignKey("fin_accounts.acc_code"))
    voucher_no = Column(String(50))          # เลขที่ใบเบิก
    voucher_date = Column(Date)              # วันที่ตั้งเบิก
    payment_doc_no = Column(String(50))      # เลขที่เอกสารจ่าย
    purpose = Column(Text)                   # วัตถุประสงค์
    amount_paid = Column(Numeric(12, 2))     # ยอดเงิน — NULL until paid
    transfer_date = Column(Date)             # วันที่โอนเงิน — NULL until paid
    clear_due_date = Column(Date)            # กำหนดการเคลียร์ — NULL until paid
    paid_by = Column(String(50))
    paid_at = Column(DateTime(timezone=True))

    # step 4 — requester clears
    clear_date = Column(Date)                # วันที่ส่งเอกสารเคลียร์
    amount_actual = Column(Numeric(12, 2))   # ยอดใช้จริง
    clear_doc_no = Column(String(100))       # เอกสารเคลียร์
    settle_amount = Column(Numeric(12, 2))   # รับคืน (+) / เบิกเพิ่ม (−)
    settle_date = Column(Date)               # วันที่โอนเงินคืน
    remark = Column(Text)                    # หมายเหตุ
    clear_submitted_at = Column(DateTime(timezone=True))

    # step 5 — Finance checks
    review_remark = Column(Text)
    closed_by = Column(String(50))
    closed_at = Column(DateTime(timezone=True))

    fin_status = Column(String(30), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("amount_paid >= 0", name="ck_fin_advances_amount_paid"),
        CheckConstraint("amount_actual IS NULL OR amount_actual >= 0", name="ck_fin_advances_amount_actual"),
        CheckConstraint("fin_status IN ('VOUCHERED','VOUCHER_REJECTED','PAID','CLEARING_SUBMITTED','SENT_BACK','CLOSED')",
                        name="ck_fin_advances_fin_status"),
        Index("ix_fin_advances_fin_status", "fin_status"),
        Index("ix_fin_advances_clear_due_date", "clear_due_date"),
    )


class FinAdvanceLog(Base):
    __tablename__ = "fin_advance_logs"

    id = Column(Integer, primary_key=True)
    advance_id = Column(Integer, ForeignKey("fin_advances.id", ondelete="CASCADE"), nullable=False, index=True)
    action = Column(String(30), nullable=False)  # PAY / PAY_EDIT / CLEAR_SUBMIT / CLEAR_EDIT / SEND_BACK / CONFIRM
    changes = Column(JSONB)
    remark = Column(Text)
    action_by = Column(String(50))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class FinApprovalTier(Base):
    """ADV approval by amount (clause 6). Created by scripts/migrations/2026-09-29_finance_advance_v2.sql."""
    __tablename__ = "fin_approval_tiers"

    clause = Column(String(10), primary_key=True)
    amount_max = Column(Numeric(14, 2))           # NULL = no cap
    min_level = Column(Integer, nullable=False)
    approver_label = Column(String(120), nullable=False)
    sort_order = Column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("min_level BETWEEN 1 AND 20", name="ck_fin_approval_tiers_min_level"),
    )


class FinAdvanceClearItem(Base):
    """Clearing expense lines (A/B/C/D/E). Created by scripts/migrations/2026-10-01_finance_advance_v2d_clear_items.sql."""
    __tablename__ = "fin_advance_clear_items"

    id = Column(Integer, primary_key=True)
    advance_id = Column(Integer, ForeignKey("fin_advances.id", ondelete="CASCADE"), nullable=False)
    line_no = Column(Integer, nullable=False)
    expense_date = Column(Date, nullable=False)
    vehicle = Column(String(50))
    has_receipt = Column(Boolean, nullable=False, default=True, server_default="true")
    description = Column(String(255), nullable=False)
    amount_before_vat = Column(Numeric(12, 2), nullable=False)   # A
    vat_amount = Column(Numeric(12, 2), nullable=False)          # B
    total_amount = Column(Numeric(12, 2), nullable=False)        # C = A + B
    wht_amount = Column(Numeric(12, 2), nullable=False)          # D
    net_amount = Column(Numeric(12, 2), nullable=False)          # E = C - D
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("amount_before_vat >= 0 AND vat_amount >= 0 AND total_amount >= 0 "
                        "AND wht_amount >= 0 AND net_amount >= 0", name="ck_fin_advance_clear_items_amounts"),
        Index("ix_fin_advance_clear_items_advance_id", "advance_id"),
    )


class FinPayeeAccountRequest(Base):
    """Employee K-Bank account change request. Created by scripts/migrations/2026-10-05_finance_payee_accounts.sql."""
    __tablename__ = "fin_payee_account_requests"

    id = Column(Integer, primary_key=True)
    employee_id = Column(String(50), nullable=False)
    bank = Column(String(20), nullable=False, default="KBANK", server_default="KBANK")
    account_no = Column(String(20), nullable=False)
    account_name = Column(String(150), nullable=False)
    remark = Column(Text)
    status = Column(String(10), nullable=False, default="PENDING", server_default="PENDING")
    review_remark = Column(Text)
    reviewed_by = Column(String(50))
    reviewed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("bank = 'KBANK'", name="ck_fin_payee_requests_bank"),
        CheckConstraint("status IN ('PENDING','APPROVED','REJECTED','CANCELLED')", name="ck_fin_payee_requests_status"),
        Index("uq_fin_payee_requests_one_pending", "employee_id", unique=True,
              postgresql_where=text("status = 'PENDING'")),
    )


class FinPayeeAccount(Base):
    """Master of employee K-Bank accounts (one row per employee)."""
    __tablename__ = "fin_payee_accounts"

    id = Column(Integer, primary_key=True)
    employee_id = Column(String(50), nullable=False, unique=True)
    bank = Column(String(20), nullable=False, default="KBANK", server_default="KBANK")
    account_no = Column(String(20), nullable=False)
    account_name = Column(String(150), nullable=False)
    status = Column(String(10), nullable=False, default="ACTIVE", server_default="ACTIVE")
    source_request_id = Column(Integer, ForeignKey("fin_payee_account_requests.id", ondelete="SET NULL"))
    created_by = Column(String(50))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_by = Column(String(50))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("bank = 'KBANK'", name="ck_fin_payee_accounts_bank"),
        CheckConstraint("status IN ('ACTIVE','INACTIVE')", name="ck_fin_payee_accounts_status"),
    )


class FinPayeeAccountLog(Base):
    """Audit log; changes = {field: [before, after]} pairs."""
    __tablename__ = "fin_payee_account_logs"

    id = Column(Integer, primary_key=True)
    employee_id = Column(String(50), nullable=False)
    action = Column(String(30), nullable=False)  # REQUEST / REQUEST_CANCEL / REQUEST_REJECT / APPROVE / CREATE / UPDATE / DEACTIVATE / REACTIVATE
    changes = Column(JSONB)
    remark = Column(Text)
    action_by = Column(String(50))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_fin_payee_account_logs_employee_id", "employee_id"),
    )
