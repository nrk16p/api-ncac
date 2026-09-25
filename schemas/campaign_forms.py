"""
Schemas — แบบฟอร์มแคมเปญ (หน้า /campaigns แท็บ "ฟอร์ม" ของ mena-next-lb + Mena-go)

โครงตรงกับ mena-next-lb/src/components/campaign-forms/types.ts
spec: mena-next-lb/docs/campaign-forms-backend.md
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, field_validator, model_validator

QuestionType = Literal[
    "short", "paragraph", "single", "multiple", "date", "datetime", "point", "ranking"
]

OPTION_TYPES = ("single", "multiple", "ranking")
RANK_MIN_OPTIONS = 2
RANK_MAX_OPTIONS = 5


def _clean_text(value: Optional[str]) -> str:
    return (value or "").strip()


class FormQuestion(BaseModel):
    # id ถูกใช้เป็น key ของ answers ใน Mongo — ห้ามมี '.' หรือขึ้นต้นด้วย '$'
    id: str = Field(..., pattern=r"^[A-Za-z0-9_-]{1,64}$", description="คงที่ตลอดอายุฟอร์ม")
    label: str = Field(..., max_length=500)
    hint: Optional[str] = Field(None, max_length=500)
    type: QuestionType
    required: bool = False
    options: List[str] = Field(default_factory=list, description="เฉพาะ single / multiple / ranking")
    scale_labels: Optional[Tuple[str, str]] = Field(None, description="เฉพาะ point — ป้ายคะแนน 1 และ 5")

    @model_validator(mode="after")
    def _normalize(self) -> "FormQuestion":
        self.label = _clean_text(self.label)
        if not self.label:
            raise ValueError(f"คำถาม {self.id}: ต้องมีข้อความคำถาม")
        self.hint = _clean_text(self.hint) or None

        if self.type in OPTION_TYPES:
            options = [_clean_text(o) for o in self.options]
            if any(not o for o in options):
                raise ValueError(f"คำถาม {self.id}: ตัวเลือกต้องไม่ว่าง")
            if len(set(options)) != len(options):
                raise ValueError(f"คำถาม {self.id}: ตัวเลือกซ้ำกัน")
            if self.type == "ranking" and not RANK_MIN_OPTIONS <= len(options) <= RANK_MAX_OPTIONS:
                raise ValueError(
                    f"คำถาม {self.id}: จัดอันดับต้องมี {RANK_MIN_OPTIONS}–{RANK_MAX_OPTIONS} ตัวเลือก"
                )
            if not options:
                raise ValueError(f"คำถาม {self.id}: ต้องมีตัวเลือกอย่างน้อย 1 ข้อ")
            self.options = options
        else:
            self.options = []

        if self.type != "point":
            self.scale_labels = None
        return self


def _check_unique_ids(questions: List[FormQuestion]) -> List[FormQuestion]:
    ids = [q.id for q in questions]
    if len(set(ids)) != len(ids):
        raise ValueError("รหัสคำถาม (id) ซ้ำกัน")
    return questions


def _require_name(value: str) -> str:
    value = _clean_text(value)
    if not value:
        raise ValueError("ต้องมีชื่อฟอร์ม")
    return value


class CampaignFormCreate(BaseModel):
    name: str = Field(..., max_length=200)
    description: str = Field("", max_length=2000)
    questions: List[FormQuestion] = Field(..., min_length=1)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _require_name(v)

    @field_validator("description")
    @classmethod
    def _description(cls, v: str) -> str:
        return _clean_text(v)

    @field_validator("questions")
    @classmethod
    def _ids(cls, v: List[FormQuestion]) -> List[FormQuestion]:
        return _check_unique_ids(v)


class CampaignFormUpdate(BaseModel):
    """PATCH — ส่งมาเฉพาะฟิลด์ที่แก้"""

    name: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    questions: Optional[List[FormQuestion]] = Field(None, min_length=1)

    @field_validator("name")
    @classmethod
    def _name(cls, v: Optional[str]) -> Optional[str]:
        return None if v is None else _require_name(v)

    @field_validator("description")
    @classmethod
    def _description(cls, v: Optional[str]) -> Optional[str]:
        return None if v is None else _clean_text(v)

    @field_validator("questions")
    @classmethod
    def _ids(cls, v: Optional[List[FormQuestion]]) -> Optional[List[FormQuestion]]:
        return None if v is None else _check_unique_ids(v)


class ApprovePayload(BaseModel):
    approved: bool


class CampaignFormOut(BaseModel):
    id: str = Field(..., description="เท่ากับ form_id — ให้ตรงกับ type ฝั่ง frontend")
    form_id: str
    name: str
    description: str = ""
    questions: List[FormQuestion]
    created_by: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    response_count: int = 0


class PublicFormOut(BaseModel):
    """ส่งให้ Mena-go — ไม่มีข้อมูลฝั่งผู้ดูแล"""

    id: str
    form_id: str
    name: str
    description: str = ""
    questions: List[FormQuestion]


class AnswerSubmit(BaseModel):
    respondent: str = Field(..., max_length=200)
    drivercode: Optional[str] = Field(None, max_length=50)
    line_user_id: Optional[str] = Field(None, max_length=100)
    campaign_id: Optional[str] = Field(None, max_length=100, description="แคมเปญที่เปิดฟอร์มนี้")
    answers: Dict[str, Any] = Field(default_factory=dict, description="key = question id")

    @field_validator("respondent")
    @classmethod
    def _respondent(cls, v: str) -> str:
        v = _clean_text(v)
        if not v:
            raise ValueError("ต้องมีชื่อผู้ตอบ")
        return v

    @field_validator("drivercode", "line_user_id", "campaign_id")
    @classmethod
    def _blank_to_none(cls, v: Optional[str]) -> Optional[str]:
        return _clean_text(v) or None


class AnswerOut(BaseModel):
    id: str
    form_id: str
    respondent: str
    drivercode: Optional[str] = None
    line_user_id: Optional[str] = None
    campaign_id: Optional[str] = None
    submitted_at: datetime
    answers: Dict[str, Any]


class AnswerSubmitResult(BaseModel):
    id: str
    form_id: str
    submitted_at: datetime
