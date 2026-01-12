from pydantic import BaseModel
from typing import Optional
from datetime import datetime


# ============================================================
# Create Approval Rule
# ============================================================
class FormApprovalRuleCreate(BaseModel):
    form_code: str
    level_no: int
    approve_by_type: str      # "position_level"
    approve_by_value: int     # เช่น 5 = Manager
    same_department: bool = True
    is_active: bool = True


# ============================================================
# Approval Action (Approve / Reject)
# ============================================================
class ApprovalActionRequest(BaseModel):
    employee_id: str
    remark: Optional[str] = None


# ============================================================
# Approval Log Response
# ============================================================
class FormApprovalLogResponse(BaseModel):
    id: int
    submission_id: int
    level_no: int
    action: str
    action_by_employee_id: str
    action_at: datetime
    remark: Optional[str]

    class Config:
        from_attributes = True
