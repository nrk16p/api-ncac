"""
Schemas — แบบสำรวจเสียง พจส. งานปีใหม่

รับ payload ตรงตามที่หน้า mena-go-lb/src/pages/newyear-survey.jsx ส่งมา
(ดูฟังก์ชัน buildSurveyPayload ในไฟล์นั้น)
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SurveyInfo(BaseModel):
    id: str = Field(..., description="รหัสชุดแบบสำรวจ เช่น newyear-2570-mixer")
    version: int = 1
    title: Optional[str] = None


class Respondent(BaseModel):
    employee_code: Optional[str] = Field(None, description="รหัสพนักงาน (drivercode)")
    employee_name: Optional[str] = None
    truckplate: Optional[str] = None
    plant: Optional[str] = None
    customer: Optional[str] = None
    line_user_id: Optional[str] = None


class SurveyMeta(BaseModel):
    attending: Optional[bool] = Field(None, description="ตั้งใจมาร่วมงานหรือไม่")
    path: List[str] = Field(default_factory=list, description="section ที่ผู้ตอบเดินผ่าน")
    started_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    client: Optional[str] = None


class AnswerItem(BaseModel):
    id: str = Field(..., description="รหัสข้อ เช่น q1")
    no: Optional[int] = None
    section_id: Optional[str] = None
    section: Optional[str] = None
    question: str
    type: str = Field(..., description="choice | checkbox | rank | paragraph")
    value: Any = None
    ranked: Optional[List[str]] = Field(None, description="rank: เรียงอันดับ 1→N")
    other: Optional[str] = Field(None, description="ข้อความจากช่อง 'อื่นๆ (โปรดระบุ)'")


class NewYearSurveyCreate(BaseModel):
    survey: SurveyInfo
    respondent: Respondent
    meta: SurveyMeta = Field(default_factory=SurveyMeta)
    answers: List[AnswerItem] = Field(default_factory=list)


class NewYearSurveySaveResult(BaseModel):
    success: bool = True
    created: bool = Field(..., description="True = บันทึกใหม่, False = ทับคำตอบเดิมของคนนี้")
    survey_id: str
    drivercode: str
    driver_name: Optional[str] = None
    client_name: Optional[str] = None
    plant_name: Optional[str] = None
    answered_count: int
    submitted_at: Optional[datetime] = None


class NewYearSurveyDoc(BaseModel):
    """เอกสารที่เก็บจริงใน hr_service.newyear-survey"""

    survey_id: str
    survey_version: int = 1
    survey_title: Optional[str] = None

    drivercode: str
    driver_name: Optional[str] = None
    truckplate: Optional[str] = None
    plant: Optional[str] = None
    customer: Optional[str] = None
    line_user_id: Optional[str] = None

    # ดึงจาก PostgreSQL masterdrivers ด้วย drivercode -> driver_id
    client_name: Optional[str] = None
    plant_code: Optional[str] = None
    plant_name: Optional[str] = None

    attending: Optional[bool] = None
    path: List[str] = Field(default_factory=list)

    answers: Dict[str, Any] = Field(
        default_factory=dict,
        description="key = ข้อความคำถาม, value = คำตอบ",
    )
    answers_detail: List[AnswerItem] = Field(default_factory=list)
    answered_count: int = 0

    started_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    client: Optional[str] = None

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
