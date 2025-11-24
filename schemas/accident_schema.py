from pydantic import BaseModel, field_validator
from typing import List, Optional, Union
from datetime import datetime

# ============================================================
# ACCIDENT CASE DOC SCHEMAS
# ============================================================
class AccidentCaseDocSchema(BaseModel):
    id: Optional[int] = None
    document_no_ac: Optional[str] = None
    data: dict
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        orm_mode = True


# ============================================================
# ACCIDENT CASE CREATE / UPDATE / RESPONSE
# ============================================================
class AccidentCaseCreate(BaseModel):
    site_id: int
    department_id: int
    client_id: Optional[int] = None
    origin_id: Optional[int] = None
    reporter_id: Optional[int] = None
    driver_id: Optional[str] = None
    driver_role_id: Optional[int] = None
    vehicle_id_head: Optional[int] = None
    vehicle_id_tail: Optional[int] = None
    province_id: Optional[int] = None
    district_id: Optional[int] = None
    sub_district_id: Optional[int] = None
    record_datetime: datetime
    incident_datetime: datetime
    case_location: Optional[str] = None
    destination: Optional[str] = None
    case_details: Optional[str] = None
    estimated_goods_damage_value: Optional[float] = None
    estimated_vehicle_damage_value: Optional[float] = None
    actual_goods_damage_value: Optional[float] = None
    actual_vehicle_damage_value: Optional[float] = None
    alcohol_test_result: Optional[float] = None
    drug_test_result: Optional[str] = None
    injured_not_hospitalized: Optional[int] = None
    injured_hospitalized: Optional[int] = None
    fatalities: Optional[int] = None
    docs: Optional[Union[List[AccidentCaseDocSchema], dict]] = None

    # ✅ Allow single dict or list
    @field_validator("docs", mode="before")
    @classmethod
    def ensure_list(cls, v):
        if isinstance(v, dict):
            return [v]
        return v


class AccidentCaseUpdate(BaseModel):
    case_location: Optional[str] = None
    destination: Optional[str] = None
    case_details: Optional[str] = None
    casestatus: Optional[str] = None
    priority: Optional[str] = None


class AccidentCaseResponse(BaseModel):
    accident_case_id: int
    document_no_ac: str
    site_name: Optional[str] = None
    department_name: Optional[str] = None
    client_name: Optional[str] = None
    origin_name: Optional[str] = None
    reporter_name: Optional[str] = None
    driver_name: Optional[str] = None
    driver_role_name: Optional[str] = None
    vehicle_head_plate: Optional[str] = None
    vehicle_tail_plate: Optional[str] = None
    record_datetime: Optional[datetime] = None
    incident_datetime: Optional[datetime] = None
    province_name: Optional[str] = None
    district_name: Optional[str] = None
    sub_district_name: Optional[str] = None
    case_location: Optional[str] = None
    police_station_area: Optional[str] = None
    destination: Optional[str] = None
    truck_damage: Optional[str] = None
    product_damage: Optional[str] = None
    case_details: Optional[str] = None
    estimated_goods_damage_value: Optional[float] = None
    estimated_vehicle_damage_value: Optional[float] = None
    actual_goods_damage_value: Optional[float] = None
    actual_vehicle_damage_value: Optional[float] = None
    alcohol_test_result: Optional[float] = None
    drug_test_result: Optional[str] = None
    attachments: Optional[str] = None
    casestatus: Optional[str] = None
    priority: Optional[str] = None
    docs: List[AccidentCaseDocSchema] = []  # ✅ matches model output

    class Config:
        orm_mode = True
