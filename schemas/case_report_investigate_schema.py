from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, date
from .corrective_action_schema import CorrectiveActionItem

class CaseReportInvestigateBase(BaseModel):
    root_cause_analysis: Optional[str] = None
    claim_type: Optional[str] = None
    insurance_claim: Optional[str] = None
    product_resellable: Optional[str] = None
    remaining_damage_cost: Optional[int] = None
    driver_cost: Optional[int] = None
    company_cost: Optional[int] = None

class CaseReportInvestigateCreate(CaseReportInvestigateBase):
    corrective_actions: Optional[List[CorrectiveActionItem]] = []

class CaseReportInvestigateUpdate(CaseReportInvestigateBase):
    corrective_actions: Optional[List[CorrectiveActionItem]] = None

class CaseReportInvestigateOut(CaseReportInvestigateBase):
    investigate_id: int
    document_no: str
    created_at: datetime
    updated_at: datetime
    corrective_actions: List[CorrectiveActionItem] = []
    class Config:
        from_attributes = True
