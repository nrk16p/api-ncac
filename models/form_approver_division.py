# models/form_approver_division.py

from sqlalchemy import Column, Integer, String, Boolean, DateTime, UniqueConstraint
from sqlalchemy.sql import func
from database import Base

class FormApproverDivision(Base):
    """Cross-division approver: employee_id may approve requesters whose department is in division_id."""
    __tablename__ = "form_approver_divisions"
    __table_args__ = (UniqueConstraint("employee_id", "division_id", name="uq_approver_division"),)

    id = Column(Integer, primary_key=True)
    employee_id = Column(String, nullable=False)
    division_id = Column(Integer, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
