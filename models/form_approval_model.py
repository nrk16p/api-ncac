from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


# ============================================================
# Form Approval Rule (กำหนดว่าใครต้องอนุมัติ)
# ============================================================
class FormApprovalRule(Base):
    __tablename__ = "form_approval_rules"

    id = Column(Integer, primary_key=True)
    form_master_id = Column(Integer, ForeignKey("form_masters.id"), nullable=False)

    # 🆕 creator level condition
    creator_min = Column(Integer, nullable=False)
    creator_max = Column(Integer, nullable=False)

    # approval target
    level_no = Column(Integer, nullable=False)
    approve_by_type = Column(String(30), nullable=False)   # position_level
    approve_by_value = Column(Integer, nullable=False)    # เช่น 5, 9
    same_department = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # relationship
    form_master = relationship("FormMaster", back_populates="approval_rules")
# ============================================================
# Approval Log (เก็บประวัติ approve / reject)
# ============================================================
class FormApprovalLog(Base):
    __tablename__ = "form_approval_logs"

    id = Column(Integer, primary_key=True, index=True)
    submission_id = Column(Integer, ForeignKey("form_submissions.id"), nullable=False)

    level_no = Column(Integer, nullable=False)
    action = Column(String, nullable=False)  # "Approved" | "Rejected"
    action_by_employee_id = Column(String, nullable=False)
    action_at = Column(DateTime, default=datetime.utcnow)
    remark = Column(String, nullable=True)

    submission = relationship("FormSubmission", backref="approval_logs")
