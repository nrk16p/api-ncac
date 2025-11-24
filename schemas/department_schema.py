from pydantic import BaseModel
from typing import Optional

class DepartmentBase(BaseModel):
    department_name_th: str
    department_name_en: Optional[str] = None

class DepartmentCreate(DepartmentBase):
    pass

class DepartmentResponse(DepartmentBase):
    department_id: int
    class Config:
        from_attributes = True
