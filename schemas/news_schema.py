from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class SafetyTalkNewsItem(BaseModel):
    inspection_task_id: str
    title: str
    description: str
    image_urls: List[str] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
