from pydantic import BaseModel
from typing import List, Optional, Union
from datetime import datetime

# -------------------------
# OPTIONS
# -------------------------
class FormOptionCreate(BaseModel):
    option_value: str
    option_label: str
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
class FormSubmissionValueCreate(BaseModel):
    question_id: int
    value_text: Optional[str] = None
    value_number: Optional[float] = None
    value_date: Optional[datetime] = None
    value_boolean: Optional[bool] = None

class FormSubmissionCreate(BaseModel):
    form_code: str
    created_by: str            # employee_id from FE
    updated_by: str | None = None
    values: List[FormSubmissionValueCreate]
