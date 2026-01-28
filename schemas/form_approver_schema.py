# schemas/form_approver_schema.py

from pydantic import BaseModel
from typing import List

class ApproverDepartmentCreate(BaseModel):
    employee_id: str
    department_ids: List[int]

class ApproverDepartmentOut(BaseModel):
    employee_id: str
    department_id: int
    is_active: bool
