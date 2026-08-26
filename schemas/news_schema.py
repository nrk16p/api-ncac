from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class SafetyTalkNewsItem(BaseModel):
    inspection_task_id: str
    title: str
    description: str
    image_urls: List[str] = []
    client_name: Optional[str] = None
    trainer_id: List[Optional[str]] = []
    inspection_date: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CaseReportNewsItem(BaseModel):
    case_id: int
    document_no: str
    title: str
    description: str
    image_urls: List[str] = []
    client_name: Optional[str] = None
    site: Optional[str] = None
    priority: Optional[str] = None
    casestatus: Optional[str] = None
    record_date: Optional[datetime] = None
    incident_date: Optional[datetime] = None


class AccidentCaseNewsItem(BaseModel):
    accident_case_id: int
    document_no_ac: str
    title: str
    description: str
    image_urls: List[str] = []
    client_name: Optional[str] = None
    site: Optional[str] = None
    priority: Optional[str] = None
    casestatus: Optional[str] = None
    record_datetime: Optional[datetime] = None
    incident_datetime: Optional[datetime] = None
