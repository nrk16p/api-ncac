# schemas/form_approver_schema.py

from pydantic import BaseModel
from typing import List

class ApproverDivisionCreate(BaseModel):
    employee_id: str
    division_ids: List[int]

class ApproverDivisionOut(BaseModel):
    employee_id: str
    division_id: int
    is_active: bool
