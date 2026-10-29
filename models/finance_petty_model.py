"""Finance — เบิกเงินสดย่อย. Tables are created by scripts/migrations/2026-10-12_finance_petty_cash.sql
(and create_all, which is a no-op once they exist). Keep both in sync.

petty ไม่ผูกกับ form_submissions — form_id เป็นสตริงที่ S3 ใช้เป็น prefix ของไฟล์แนบอยู่แล้ว
"""
from sqlalchemy import (
    JSON, Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB

# Postgres ได้ jsonb เหมือนที่ migration สร้าง ส่วน sqlite (เทสต์) ได้ JSON ธรรมดา
# เขียน JSONB ตรง ๆ แบบ finance_model.py เดิมทำให้ create_all บน sqlite พังทั้ง metadata
JSONish = JSON().with_variant(JSONB(), "postgresql")

from database import Base

# flow v2 (29/10/2026) · TRANSFERRED เลิกใช้แต่ยังอยู่ใน CHECK ให้แถวเก่าไม่ผิดกติกา
PETTY_STATUSES = (
    "PENDING_APPROVAL", "RETURNED", "APPROVED", "CLEAR_PENDING", "AWAITING_REVIEW", "SENT_BACK", "REVIEWED",
    "CLOSED", "TRANSFERRED",
)
PETTY_BATCH_STATUSES = ("CLEAR_PENDING", "CLEAR_REJECTED", "AWAITING_REVIEW", "REVIEWED", "VOUCHERED", "CLOSED")


def _in_list(column: str, values) -> str:
    return f"{column} IN (" + ",".join(f"'{v}'" for v in values) + ")"


class FinPettyFund(Base):
    __tablename__ = "fin_petty_funds"

    id = Column(Integer, primary_key=True)
    branch_code = Column(String(4), nullable=False)
    division_code = Column(String(8), nullable=False)
    holder_employee_id = Column(String(50), nullable=False)
    fund_limit = Column(Numeric(12, 2))
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    updated_by = Column(String(50))

    __table_args__ = (Index("ix_fin_petty_funds_holder", "holder_employee_id"),)


class FinPettyExpenseType(Base):
    __tablename__ = "fin_petty_expense_types"

    code = Column(String(20), primary_key=True)
    name_th = Column(String(120), nullable=False)
    atms_sku = Column(String(20))
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    spec = Column(JSONish, nullable=False, server_default="{}")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    updated_by = Column(String(50))


class FinPettyBatch(Base):
    __tablename__ = "fin_petty_batches"

    id = Column(Integer, primary_key=True)
    fund_id = Column(Integer, ForeignKey("fin_petty_funds.id"), nullable=False)
    batch_no = Column(String(30), nullable=False, unique=True)
    total_amount = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    remark = Column(Text)
    # รอบส่งเคลียร์ (flow v2): เจ้าของเงินสดสร้าง → หัวหน้าอนุมัติเคลียร์ → บัญชีตรวจ → ตั้งเบิก → การเงินทำจ่าย
    status = Column(String(30), nullable=False, default="CLEAR_PENDING", server_default="CLEAR_PENDING")
    submitted_by = Column(String(50))
    submitted_at = Column(DateTime(timezone=True))
    clear_required_level = Column(Integer)
    clear_approved_by = Column(String(50))
    clear_approved_at = Column(DateTime(timezone=True))
    clear_reject_reason = Column(Text)
    voucher_no = Column(String(50))
    vouchered_by = Column(String(50))
    vouchered_at = Column(DateTime(timezone=True))
    pay_date = Column(Date)
    paid_by = Column(String(50))
    paid_at = Column(DateTime(timezone=True))
    closed_by = Column(String(50))
    closed_at = Column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(_in_list("status", PETTY_BATCH_STATUSES), name="ck_fin_petty_batches_status"),
    )


class FinPettyClaim(Base):
    __tablename__ = "fin_petty_claims"

    id = Column(Integer, primary_key=True)
    form_id = Column(String(80), nullable=False, unique=True)
    fund_id = Column(Integer, ForeignKey("fin_petty_funds.id"), nullable=False)
    expense_type = Column(String(20), ForeignKey("fin_petty_expense_types.code"), nullable=False)
    expense_date = Column(Date, nullable=False)
    period_from = Column(Date)
    period_to = Column(Date)
    driver_employee_id = Column(String(50))
    driver_name = Column(String(150))
    payee_name = Column(String(150))
    amount = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    detail = Column(JSONish, nullable=False, server_default="{}")
    dd_no = Column(String(50))
    dd_snapshot = Column(JSONish)
    receipt_no = Column(String(50))
    wht_applicable = Column(Boolean, nullable=False, default=False, server_default="false")
    created_by = Column(String(50), nullable=False)
    source = Column(String(10), nullable=False, default="WEB", server_default="'WEB'")
    dup_override_reason = Column(Text)
    return_reason = Column(Text)
    approved_by = Column(String(50))
    approved_at = Column(DateTime(timezone=True))
    transfer_date = Column(Date)
    transfer_slip_at = Column(DateTime(timezone=True))
    submitted_to_acc_at = Column(DateTime(timezone=True))
    review_remark = Column(Text)
    reviewed_by = Column(String(50))
    reviewed_at = Column(DateTime(timezone=True))
    batch_id = Column(Integer, ForeignKey("fin_petty_batches.id"))
    debt_doc_no = Column(String(50))  # เลขที่ใบรับสภาพหนี้ — บังคับตอนอนุมัติเมื่อมี พจร. (อ้างอิงเลขเอกสารอย่างเดียว)
    status = Column(String(30), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(_in_list("status", PETTY_STATUSES), name="ck_fin_petty_claims_status"),
        CheckConstraint("amount >= 0", name="ck_fin_petty_claims_amount"),
        Index("ix_fin_petty_claims_fund_status", "fund_id", "status"),
        Index("ix_fin_petty_claims_status", "status"),
        Index("ix_fin_petty_claims_created_by", "created_by"),
    )


class FinPettyClaimItem(Base):
    __tablename__ = "fin_petty_claim_items"

    id = Column(Integer, primary_key=True)
    claim_id = Column(Integer, ForeignKey("fin_petty_claims.id", ondelete="CASCADE"), nullable=False)
    line_no = Column(Integer, nullable=False)
    event_date = Column(Date, nullable=False)
    truck_code = Column(String(20))
    truck_plate = Column(String(20))
    truck_type = Column(String(30))
    ref_no = Column(String(50))
    qty = Column(Numeric(10, 2), nullable=False, default=1, server_default="1")
    rate = Column(Numeric(12, 2), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    remark = Column(String(255))
    driver_name = Column(String(150))  # พจส. ต่อแถว — ใบสรุปรายการ (ผู้ใช้สั่ง 2026-10-13)
    driver_role = Column(String(10))  # "พจส." / "พจร." — มี พจร. ต้องมีเลขที่ใบรับสภาพหนี้ (29/10/2026)
    tire_position = Column(String(120))  # ใบยาง: รหัสตำแหน่งแบบ WMS คั่นด้วย , เช่น "RA1,RA2"

    __table_args__ = (
        Index("ix_fin_petty_items_claim", "claim_id"),
        Index("ix_fin_petty_items_date_code", "event_date", "truck_code"),
        Index("ix_fin_petty_items_date_plate", "event_date", "truck_plate"),
        Index("ix_fin_petty_items_hard_dup", "ref_no", "truck_code", "truck_plate"),
    )


class FinPettyLog(Base):
    __tablename__ = "fin_petty_logs"

    id = Column(Integer, primary_key=True)
    claim_id = Column(Integer, ForeignKey("fin_petty_claims.id", ondelete="CASCADE"), nullable=False, index=True)
    action = Column(String(30), nullable=False)
    changes = Column(JSONish)
    remark = Column(Text)
    action_by = Column(String(50))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
