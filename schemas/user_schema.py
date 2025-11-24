from pydantic import BaseModel
from typing import Optional


class UserBase(BaseModel):
    username: str
    firstname: Optional[str]
    lastname: Optional[str]
    employee_id: Optional[str]
    department_id: Optional[int]
    site_id: Optional[int]
    position_id: Optional[int]


class UserCreate(UserBase):
    password: str


class UserResponse(UserBase):
    id: int

    class Config:
        from_attributes = True  # ✅ (แทน orm_mode)
