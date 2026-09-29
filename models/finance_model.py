"""Finance — เบิกเงิน Advance. Tables are created by scripts/migrations/2026-09-28_finance_advance.sql
(and create_all, which is a no-op once they exist). Keep both in sync."""
from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func,
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
        CheckConstraint("fin_status IN ('VOUCHERED','PAID','CLEARING_SUBMITTED','SENT_BACK','CLOSED')",
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
