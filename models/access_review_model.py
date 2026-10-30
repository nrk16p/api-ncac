"""Access Review (ทบทวนสิทธิ์ผู้ใช้) — ATMS / Winspeed / Email permission review.

Migration: scripts/migrations/2026-10-29_access_review.sql (same DDL + ar_systems seed).
employee_id columns hold users.employee_id (string, no FK) like form_approver_divisions.
Roles and user_roles are soft-deleted (is_active=false) so review lines keep their history.
"""
from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, true,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base


class ArSystem(Base):
    __tablename__ = "ar_systems"

    id = Column(Integer, primary_key=True)
    code = Column(String(30), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True, server_default=true())


class ArRole(Base):
    __tablename__ = "ar_roles"
    __table_args__ = (UniqueConstraint("system_id", "name", name="uq_ar_roles_system_name"),)

    id = Column(Integer, primary_key=True)
    system_id = Column(Integer, ForeignKey("ar_systems.id"), nullable=False)
    name = Column(String(500), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True, server_default=true())

    system = relationship("ArSystem")


class ArRoleItem(Base):
    __tablename__ = "ar_role_items"
    __table_args__ = (Index("ix_ar_role_items_role_id", "role_id"),)

    id = Column(Integer, primary_key=True)
    role_id = Column(Integer, ForeignKey("ar_roles.id"), nullable=False)
    category = Column(String(500), nullable=False)
    item = Column(String(500))
    permission = Column(String(255))
    # false = gone from the latest import but still referenced by a review (ar_review_line_items) → kept for history
    is_active = Column(Boolean, nullable=False, default=True, server_default=true())


class ArUserRole(Base):
    """One permission an employee holds: role (→ system via ar_roles.system_id) + the login it is held under."""
    __tablename__ = "ar_user_roles"
    __table_args__ = (
        UniqueConstraint("employee_id", "role_id", name="uq_ar_user_roles_employee_role"),
        Index("ix_ar_user_roles_employee_id", "employee_id"),
        Index("ix_ar_user_roles_role_id", "role_id"),
    )

    id = Column(Integer, primary_key=True)
    employee_id = Column(String(50), nullable=False)  # users.employee_id
    role_id = Column(Integer, ForeignKey("ar_roles.id"), nullable=False)
    account_name = Column(String(255))  # ATMS username / Winspeed User ID; NULL for EMAIL (users.email)
    is_active = Column(Boolean, nullable=False, default=True, server_default=true())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    role = relationship("ArRole")


class ArCycle(Base):
    __tablename__ = "ar_cycles"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)  # e.g. 'H2-2569'
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    is_open = Column(Boolean, nullable=False, default=True, server_default=true())


class ArReview(Base):
    __tablename__ = "ar_reviews"
    __table_args__ = (
        UniqueConstraint("cycle_id", "employee_id", name="uq_ar_reviews_cycle_employee"),
        Index("ix_ar_reviews_status", "status"),
        CheckConstraint("status IN ('draft', 'pending', 'approved', 'rejected')", name="ck_ar_reviews_status"),
        CheckConstraint("route IS NULL OR route IN ('dept_head', 'division', 'it_queue', 'bypass')",
                        name="ck_ar_reviews_route"),
    )

    id = Column(Integer, primary_key=True)
    cycle_id = Column(Integer, ForeignKey("ar_cycles.id"), nullable=False)
    employee_id = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False, default="draft", server_default="draft")  # draft|pending|approved|rejected
    route = Column(String(20))  # dept_head|division|it_queue|bypass
    submitted_at = Column(DateTime(timezone=True))
    decided_by = Column(String(50))
    decided_at = Column(DateTime(timezone=True))
    comment = Column(Text)  # ข้อเสนอแนะ (ผู้อนุมัติ)
    requester_note = Column(Text)  # ข้อเสนอแนะ (ผู้แจ้ง)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    cycle = relationship("ArCycle")


class ArReviewLine(Base):
    __tablename__ = "ar_review_lines"
    __table_args__ = (
        UniqueConstraint("review_id", "user_role_id", name="uq_ar_review_lines_review_user_role"),
        Index("ix_ar_review_lines_user_role_id", "user_role_id"),
        CheckConstraint("decision IS NULL OR decision IN ('keep', 'remove')", name="ck_ar_review_lines_decision"),
    )

    id = Column(Integer, primary_key=True)
    review_id = Column(Integer, ForeignKey("ar_reviews.id"), nullable=False)
    user_role_id = Column(Integer, ForeignKey("ar_user_roles.id"), nullable=False)
    decision = Column(String(10))  # keep (maybe with unticked items) | remove (whole role unticked)
    note = Column(Text)


class ArReviewLineItem(Base):
    """An item the requester UNTICKED on that line (ticked items are not stored)."""
    __tablename__ = "ar_review_line_items"
    __table_args__ = (Index("ix_ar_review_line_items_role_item_id", "role_item_id"),)

    review_line_id = Column(Integer, ForeignKey("ar_review_lines.id", ondelete="CASCADE"), primary_key=True)
    role_item_id = Column(Integer, ForeignKey("ar_role_items.id"), primary_key=True)


class ArReviewApprover(Base):
    __tablename__ = "ar_review_approvers"
    __table_args__ = (Index("ix_ar_review_approvers_employee_id", "employee_id"),)

    review_id = Column(Integer, ForeignKey("ar_reviews.id"), primary_key=True)
    employee_id = Column(String(50), primary_key=True)
    mailed_at = Column(DateTime(timezone=True))
