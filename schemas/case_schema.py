from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class CaseReportDocSchema(BaseModel):
    data: dict

    class Config:
        orm_mode = True


class CaseReportCreate(BaseModel):
    site_id: int
    department_id: int
    reporter_id: int
    driver_id: Optional[int]
    driver_role_id: Optional[int]
    record_date: Optional[datetime]
    incident_date: Optional[datetime]
    case_location: Optional[str]
    destination: Optional[str]
    case_details: Optional[str]
    estimated_cost: Optional[float]
    actual_price: Optional[float]
    attachments: Optional[str]
    priority: Optional[str] = "Minor"
    casestatus: Optional[str] = "Pending"
    docs: Optional[List[CaseReportDocSchema]] = []


class CaseReportUpdate(BaseModel):
    record_date: Optional[datetime]
    incident_date: Optional[datetime]
    case_details: Optional[str]
    casestatus: Optional[str]
    priority: Optional[str]


class CaseReportResponse(BaseModel):
    case_id: int
    document_no: str
    site_name: Optional[str]
    department_name: Optional[str]
    reporter_name: Optional[str]
    case_details: Optional[str]
    attachments: Optional[str]
    casestatus: Optional[str]
    priority: Optional[str]
    docs: List[CaseReportDocSchema] = []

    class Config:
        orm_mode = True
