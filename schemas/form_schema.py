from pydantic import BaseModel
from typing import List, Optional, Union
from datetime import datetime


# -------------------------
# OPTIONS
# -------------------------
class FormOptionCreate(BaseModel):
    option_value: str
    option_label: str
    option_filter: str | None = None

    sort_order: Optional[int] = 0

# -------------------------
# QUESTIONS
# -------------------------
class FormQuestionCreate(BaseModel):
    question_name: str
    question_label: str
    question_type: str   # text, number, dropdown, multiselect, etc.
    is_required: bool = False
    sort_order: int = 0
    options: Optional[List[FormOptionCreate]] = []

# -------------------------
# FORM MASTER
# -------------------------
class FormMasterCreate(BaseModel):
    form_type: str
    form_code: str
    form_name: Optional[str]
    form_status: Optional[str] = "Draft"
    need_approval: bool = True      # ✅ ADD THIS

    questions: List[FormQuestionCreate]

# -------------------------
# SUBMISSION
# -------------------------
class FormQuestionOut(BaseModel):
    id: int
    question_name: str
    question_label: str
    question_type: str

    class Config:
        orm_mode = True
class FormValueResponse(BaseModel):
    question_id: int
    value_text: Optional[str]
    value_number: Optional[float]
    value_date: Optional[datetime]
    value_boolean: Optional[bool]

    # 👇 flatten จาก question
    question_name: Optional[str]
    question_label: Optional[str]
    question_type: Optional[str]

    class Config:
        orm_mode = True



class FormSubmissionValueCreate(BaseModel):
    question_id: int
    value_text: Union[str, List[str], None] = None   # 👈 accept array
    value_number: Optional[float] = None
    value_date: Optional[datetime] = None
    value_boolean: Optional[bool] = None

class FormSubmissionCreate(BaseModel):
    form_code: str
    created_by: str            # employee_id from FE
    updated_by: str | None = None
    values: List[FormSubmissionValueCreate]


class FormApprovalRuleBase(BaseModel):
    form_code: str
    level_no: int
    approve_by_type: str           # e.g. "position_level"
    approve_by_value: int          # e.g. 5
    same_department: bool = False
    is_active: bool = True

class FormApprovalRuleCreate(BaseModel):
    form_code: str
    level_no: int

    # Creator mapping
    creator_min: int
    creator_max: int

    # Approver policy
    approve_by_type: str  # "position_level" | "position_level_range" | "auto"
    approve_by_value: Optional[int] = None
    approve_by_min: Optional[int] = None
    approve_by_max: Optional[int] = None

    same_department: Optional[bool] = False
    is_active: Optional[bool] = True
    
class FormApprovalRuleUpdate(BaseModel):
    level_no: Optional[int] = None
    approve_by_type: Optional[str] = None
    approve_by_value: Optional[int] = None
    same_department: Optional[bool] = None
    is_active: Optional[bool] = None


class FormMasterOut(BaseModel):
    id: int
    form_type: str
    form_code: str
    form_name: str

    class Config:
        orm_mode = True


class FormResponse(BaseModel):
    form_id: str
    status: Optional[str]                 # ✅ NEW
    status_approve: str
    created_by: str
    created_at: datetime

    # 👇 flatten จาก form_master
    form_type: Optional[str]
    form_code: Optional[str]
    form_name: Optional[str]

    values: List[FormValueResponse] = []

    class Config:
        orm_mode = True

