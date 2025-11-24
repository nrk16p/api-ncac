from pydantic import BaseModel
from typing import Optional
from datetime import date

class CorrectiveActionItem(BaseModel):
    id: Optional[int] = None
    corrective_action: Optional[str] = None
    pic_contract: Optional[str] = None
    plan_date: Optional[date] = None
    action_completed_date: Optional[date] = None

    class Config:
        from_attributes = True
