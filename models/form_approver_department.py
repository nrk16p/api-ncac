# models/form_approver_department.py

from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.sql import func
from database import Base

class FormApproverDepartment(Base):
    __tablename__ = "form_approver_departments"

    id = Column(Integer, primary_key=True)
    employee_id = Column(String, nullable=False)
    department_id = Column(Integer, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
