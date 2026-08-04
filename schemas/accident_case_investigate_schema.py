"""
Schemas สำหรับ AC Investigation Report (Part 2)

ชื่อฟิลด์ทั้งหมดตรงกับ interface `investigate_AC` ของ FE (lib/caseReport.ts)
เพื่อให้ส่ง state จากฟอร์มกลับมาได้ตรง ๆ โดยไม่ต้อง map
"""

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, field_validator


# ------------------------------------------------------------
# HELPER : FE ส่ง "" มาแทน null สำหรับช่องวันที่ที่ยังไม่กรอก
# ------------------------------------------------------------
def _blank_to_none(value):
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


class _ChildBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None      # uid ที่ FE สร้าง (server เติมให้ถ้าไม่ส่งมา)
    seq: Optional[int] = None


# ============================================================
# CHILD ITEMS
# ============================================================
class ACWhyItem(_ChildBase):
    problem: Optional[str] = None
    cause: Optional[str] = None


class ACRootCauseItem(_ChildBase):
    root_cause: Optional[str] = None
    category: Optional[str] = None     # 5M1E


class ACMeasureItem(_ChildBase):
    root_cause_id: Optional[str] = None
    measure: Optional[str] = None
    pic_contract: Optional[str] = None
    plan_date: Optional[date] = None
    action_completed_date: Optional[date] = None

    @field_validator("plan_date", "action_completed_date", mode="before")
    @classmethod
    def _empty_date(cls, v):
        return _blank_to_none(v)


class ACInvestigatorItem(_ChildBase):
    name: Optional[str] = None
    position: Optional[str] = None


# ============================================================
# PARENT
# ============================================================
class AccidentCaseInvestigateBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    accident_types: Optional[List[str]] = None
    severity_level: Optional[str] = None
    accident_description: Optional[str] = None

    risk_assessment_completed: Optional[str] = None
    risk_assessment_reviewed: Optional[str] = None
    risk_assessment_result: Optional[str] = None
    risk_assessment_date: Optional[date] = None
    risk_assessment_team: Optional[str] = None
    risk_assessment_attached: Optional[str] = None

    avoidability: Optional[str] = None

    @field_validator("risk_assessment_date", mode="before")
    @classmethod
    def _empty_date(cls, v):
        return _blank_to_none(v)


class AccidentCaseInvestigateCreate(AccidentCaseInvestigateBase):
    """POST — ไม่ส่ง list มา = ถือว่าไม่มีรายการ"""

    why_analysis: List[ACWhyItem] = []
    root_causes: List[ACRootCauseItem] = []
    measures: List[ACMeasureItem] = []
    investigators: List[ACInvestigatorItem] = []


class AccidentCaseInvestigateUpdate(AccidentCaseInvestigateBase):
    """PUT — list ที่เป็น None = ไม่แตะของเดิม, list ที่ส่งมา = แทนที่ทั้งชุด"""

    why_analysis: Optional[List[ACWhyItem]] = None
    root_causes: Optional[List[ACRootCauseItem]] = None
    measures: Optional[List[ACMeasureItem]] = None
    investigators: Optional[List[ACInvestigatorItem]] = None


class AccidentCaseInvestigateOut(AccidentCaseInvestigateBase):
    model_config = ConfigDict(from_attributes=True)

    # FE อ่าน `investigate_id` จาก response หลังบันทึก
    investigate_id: int
    document_no_ac: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    why_analysis: List[ACWhyItem] = []
    root_causes: List[ACRootCauseItem] = []
    measures: List[ACMeasureItem] = []
    investigators: List[ACInvestigatorItem] = []

    # สถานะความครบถ้วนของการสอบสวน (ใช้ตัดสิน casestatus)
    is_complete: bool = False


class AccidentCaseInvestigateSummary(BaseModel):
    """แถวสรุปสำหรับหน้า list — ไม่ดึงตารางลูกทั้งหมด"""

    model_config = ConfigDict(from_attributes=True)

    investigate_id: int
    document_no_ac: str
    severity_level: Optional[str] = None
    avoidability: Optional[str] = None
    accident_types: Optional[List[str]] = None
    root_cause_count: int = 0
    measure_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
