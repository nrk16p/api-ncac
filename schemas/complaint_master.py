from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class ComplaintMasterCreate(BaseModel):
    department_id: int
    name: str = Field(min_length=1, max_length=255)
    icon: Optional[str] = Field(default=None, max_length=50)
    sort_order: int = 0
    is_active: bool = True


class ComplaintMasterUpdate(BaseModel):
    """ทุกฟิลด์ optional — route ใช้ exclude_unset ส่งมาเฉพาะช่องที่จะแก้ได้"""
    department_id: Optional[int] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    icon: Optional[str] = Field(default=None, max_length=50)
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class ComplaintMasterResponse(BaseModel):
    id: int
    department_id: int
    name: str
    icon: Optional[str] = None
    sort_order: int
    is_active: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        # pydantic v2 — ที่อื่นในโปรเจกต์ยังเขียน orm_mode ซึ่งเป็นชื่อเก่าของคีย์นี้
        from_attributes = True


class ComplaintMasterBrief(BaseModel):
    """ตัวย่อที่ติดไปกับคำร้อง — พอให้หน้าจอวาดป้ายได้โดยไม่ต้องยิงอีกรอบ"""
    id: int
    department_id: int
    name: str
    icon: Optional[str] = None

    class Config:
        # pydantic v2 — ที่อื่นในโปรเจกต์ยังเขียน orm_mode ซึ่งเป็นชื่อเก่าของคีย์นี้
        from_attributes = True
