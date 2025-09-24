from pydantic import BaseModel
from typing import Optional, List

# -----------------
# Register
# -----------------
class RegisterRequest(BaseModel):
    username: str
    password: str
    firstname: str
    lastname: str
    employee_id: Optional[str] = None
    department_id: Optional[int] = None
    site_id: Optional[int] = None
    position_id: Optional[int] = None

# -----------------
# Position Level
# -----------------
class PositionLevelBase(BaseModel):
    level_name: str

class PositionLevelCreate(PositionLevelBase):
    pass

class PositionLevel(PositionLevelBase):
    position_level_id: int

    class Config:
        orm_mode = True

# -----------------
# Position
# -----------------
class PositionBase(BaseModel):
    position_name_th: str
    position_name_en: str
    position_level_id: Optional[int]

class PositionCreate(PositionBase):
    pass

class Position(PositionBase):
    position_id: int
    # 👇 ใช้ level แทน position_level (ตรงกับ models.Position.level)
    level: Optional[PositionLevel] = None

    class Config:
        orm_mode = True

# -----------------
# User
# -----------------
class UserBase(BaseModel):
    username: str
    firstname: Optional[str] = None
    lastname: Optional[str] = None
    employee_id: Optional[str] = None
    department_id: Optional[int] = None
    site_id: Optional[int] = None
    position_id: Optional[int] = None

class UserCreate(UserBase):
    password: str

class User(UserBase):
    id: int
    # ถ้าอยาก return relation กำหนดเพิ่มได้ เช่น:
    position: Optional[Position] = None

    class Config:
        orm_mode = True
