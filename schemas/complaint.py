from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from models.complaint import ComplaintStatus, ReviewStatus


class ComplaintCreate(BaseModel):
    driver_id: str
    subject: str
    detail: str
    complaint_type: Optional[str] = None
    complaint_details: Optional[str] = None
    complaint_url: Optional[str] = None


class ComplaintResponse(BaseModel):
    id: int
    tracking_no: str
    status: str
    created_at: datetime

    class Config:
        orm_mode = True


class ReviewCreate(BaseModel):
    level: int
    reviewer_employee_id: str

class ComplaintUpdate(BaseModel):
    driver_id: Optional[str] = None
    department_id: Optional[int] = None
    subject: Optional[str] = None
    detail: Optional[str] = None
    complaint_type: Optional[str] = None
    complaint_details: Optional[str] = None
    complaint_url: Optional[str] = None
    problem: Optional[str] = None
    solution: Optional[str] = None
    solution_url: Optional[str] = None
    result: Optional[str] = None

class ComplaintReviewOut(BaseModel):
    id: int
    level: int
    reviewer_employee_id: Optional[str]
    status: ReviewStatus
    remark: Optional[str]
    reviewed_at: Optional[datetime]

    class Config:
        orm_mode = True


class ComplaintOut(BaseModel):
    id: int
    tracking_no: str
    driver_id: str
    department_id: Optional[int]
    subject: str
    detail: str
    status: ComplaintStatus
    created_at: datetime

    reviews: List[ComplaintReviewOut] = []

    class Config:
        orm_mode = True