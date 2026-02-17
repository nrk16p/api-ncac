from sqlalchemy import (
    Column, Integer, String, Text, ForeignKey,
    DateTime, Enum, Boolean
)
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base
import enum


# =========================
# ENUMS
# =========================
class ComplaintStatus(str, enum.Enum):
    OPEN = "OPEN"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    PENDING_REVIEW = "PENDING_REVIEW"
    READY_TO_CLOSE = "READY_TO_CLOSE"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"


class ReviewStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SKIPPED = "SKIPPED"


# =========================
# DRIVER COMPLAINT
# =========================
class DriverComplaint(Base):
    __tablename__ = "driver_complaints"

    id = Column(Integer, primary_key=True, index=True)
    tracking_no = Column(String(20), unique=True, nullable=False)

    driver_id = Column(String(50), nullable=False)
    department_id = Column(Integer, ForeignKey("departments.department_id"), nullable=True)

    subject = Column(String(255), nullable=False)
    detail = Column(Text, nullable=False)

    complaint_type = Column(String(100))
    complaint_details = Column(Text)
    complaint_url = Column(String(500))

    status = Column(Enum(ComplaintStatus), default=ComplaintStatus.OPEN)

    is_deleted = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    # 👇 IMPORTANT: Must match back_populates below
    reviews = relationship("ComplaintReview", back_populates="complaint", cascade="all, delete")
    logs = relationship("ComplaintLog", back_populates="complaint", cascade="all, delete")


# =========================
# REVIEW TABLE
# =========================
class ComplaintReview(Base):
    __tablename__ = "complaint_reviews"

    id = Column(Integer, primary_key=True)
    complaint_id = Column(Integer, ForeignKey("driver_complaints.id", ondelete="CASCADE"))

    level = Column(Integer, nullable=False)

    reviewer_employee_id = Column(String(50), ForeignKey("users.employee_id"))
    status = Column(Enum(ReviewStatus), default=ReviewStatus.PENDING)

    remark = Column(Text)
    reviewed_at = Column(DateTime)

    created_at = Column(DateTime, default=datetime.utcnow)

    # 👇 MUST MATCH reviews property above
    complaint = relationship("DriverComplaint", back_populates="reviews")


# =========================
# LOG TABLE
# =========================
class ComplaintLog(Base):
    __tablename__ = "complaint_logs"

    id = Column(Integer, primary_key=True)
    complaint_id = Column(Integer, ForeignKey("driver_complaints.id", ondelete="CASCADE"))

    action = Column(String(100))
    action_by_employee_id = Column(String(50), ForeignKey("users.employee_id"))
    remark = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)

    # 👇 MUST MATCH logs property above
    complaint = relationship("DriverComplaint", back_populates="logs")
