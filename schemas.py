from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, date

# ============================================================
# REGISTER
# ============================================================
class RegisterRequest(BaseModel):
    username: str
    password: str
    firstname: str
    lastname: str
    employee_id: Optional[str] = None
    department_id: Optional[int] = None
    site_id: Optional[int] = None
    position_id: Optional[int] = None


# ============================================================
# POSITION LEVEL
# ============================================================
class PositionLevelBase(BaseModel):
    level_name: str

class PositionLevelCreate(PositionLevelBase):
    pass

class PositionLevel(PositionLevelBase):
    position_level_id: int

    class Config:
        orm_mode = True


# ============================================================
# POSITION
# ============================================================
class PositionBase(BaseModel):
    position_name_th: str
    position_name_en: str
    position_level_id: Optional[int]

class PositionCreate(PositionBase):
    pass

class Position(PositionBase):
    position_id: int
    level: Optional[PositionLevel] = None
    class Config:
        orm_mode = True


# ============================================================
# USER
# ============================================================
class UserBase(BaseModel):
    username: str
    firstname: Optional[str] = None
    lastname: Optional[str] = None
    employee_id: Optional[str] = None
    department_id: Optional[int] = None
    site_id: Optional[int] = None
    position_id: Optional[int] = None

class UserCreate(UserBase):
    password: str

class User(UserBase):
    id: int
    position: Optional[Position] = None

    class Config:
        orm_mode = True


# ============================================================
# ACCIDENT CASE BASE (Shared)
# ============================================================
class AccidentCaseBase(BaseModel):
    site_id: Optional[int] = None
    department_id: Optional[int] = None
    client_id: Optional[int] = None
    origin_id: Optional[int] = None
    reporter_id: Optional[int] = None

    record_datetime: datetime
    incident_datetime: datetime

    province_id: Optional[int] = None
    district_id: Optional[int] = None
    sub_district_id: Optional[int] = None

    case_location: Optional[str] = None
    police_station_area: Optional[str] = None
    vehicle_id_head: Optional[int] = None
    vehicle_id_tail: Optional[int] = None
    vehicle_truckno: Optional[str] = None

    driver_role_id: Optional[int] = None
    driver_id: Optional[str] = None
    case_details: Optional[str] = None

    alcohol_test: Optional[str] = None
    drug_test: Optional[str] = None
    truck_damage: Optional[str] = None
    truck_damage_details: Optional[str] = None
    product_damage: Optional[str] = None
    product_damage_details: Optional[str] = None

    injured_not_hospitalized: Optional[int] = 0
    injured_hospitalized: Optional[int] = 0
    fatalities: Optional[int] = 0
    injury_description: Optional[str] = None

    other_party_full_name: Optional[str] = None
    other_party_vehicle_plate: Optional[str] = None
    other_party_company_name: Optional[str] = None
    other_party_phone: Optional[str] = None
    other_party_insurance_name: Optional[str] = None
    other_party_claim_no: Optional[str] = None
    claim_officer_full_name: Optional[str] = None
    claim_officer_phone: Optional[str] = None

    estimated_goods_damage_value: Optional[float] = None
    estimated_vehicle_damage_value: Optional[float] = None
    actual_goods_damage_value: Optional[float] = None
    actual_vehicle_damage_value: Optional[float] = None

    attachments: Optional[str] = None
    casestatus: Optional[str] = None
    priority: Optional[str] = None

    alcohol_test_result: Optional[float] = None
    drug_test_result: Optional[str] = None


class AccidentCaseCreate(AccidentCaseBase):
    pass


class AccidentCaseUpdate(AccidentCaseBase):
    pass


class AccidentCaseDocData(BaseModel):
    warning_doc: Optional[str] = None
    warning_doc_no: Optional[str] = None
    warning_doc_remark: Optional[str] = None
    debt_doc: Optional[str] = None
    debt_doc_no: Optional[str] = None
    debt_doc_remark: Optional[str] = None
    quotation_doc: Optional[str] = None
    quotation_doc_remark: Optional[str] = None
    customer_invoice: Optional[str] = None
    customer_invoice_no: Optional[str] = None
    customer_invoice_remark: Optional[str] = None
    Insurance_claim_doc: Optional[str] = None
    Insurance_claim_doc_no: Optional[str] = None
    Insurance_claim_doc_remark: Optional[str] = None
    record_doc: Optional[str] = None
    record_doc_remark: Optional[str] = None
    medical_doc: Optional[str] = None
    medical_doc_remark: Optional[str] = None
    writeoff_doc: Optional[str] = None
    writeoff_doc_remark: Optional[str] = None
    damage_payment: Optional[str] = None
    damage_payment_no: Optional[str] = None
    damage_payment_remark: Optional[str] = None
    legal_doc: Optional[str] = None
    legal_doc_remark: Optional[str] = None
    account_attachment: Optional[str] = None
    account_attachment_no: Optional[str] = None
    account_attachment_remark: Optional[str] = None
    investigate_doc: Optional[str] = None
    investigate_doc_remark: Optional[str] = None

class AccidentCaseDocSchema(BaseModel):
    # ✅ remove metadata fields
    warning_doc: Optional[str] = None
    warning_doc_no: Optional[str] = None
    warning_doc_remark: Optional[str] = None
    debt_doc: Optional[str] = None
    debt_doc_no: Optional[str] = None
    debt_doc_remark: Optional[str] = None
    quotation_doc: Optional[str] = None
    quotation_doc_remark: Optional[str] = None
    customer_invoice: Optional[str] = None
    customer_invoice_no: Optional[str] = None
    customer_invoice_remark: Optional[str] = None
    Insurance_claim_doc: Optional[str] = None
    Insurance_claim_doc_no: Optional[str] = None
    Insurance_claim_doc_remark: Optional[str] = None
    record_doc: Optional[str] = None
    record_doc_remark: Optional[str] = None
    medical_doc: Optional[str] = None
    medical_doc_remark: Optional[str] = None
    writeoff_doc: Optional[str] = None
    writeoff_doc_remark: Optional[str] = None
    damage_payment: Optional[str] = None
    damage_payment_no: Optional[str] = None
    damage_payment_remark: Optional[str] = None
    legal_doc: Optional[str] = None
    legal_doc_remark: Optional[str] = None
    account_attachment: Optional[str] = None
    account_attachment_no: Optional[str] = None
    account_attachment_remark: Optional[str] = None
    investigate_doc: Optional[str] = None
    investigate_doc_remark: Optional[str] = None

    class Config:
        orm_mode = True


# ============================================================
# ACCIDENT CASE RESPONSE
# ============================================================
class AccidentCaseResponse(BaseModel):
    accident_case_id: int
    document_no_ac: str

    site_name: Optional[str]
    department_name: Optional[str]
    client_name: Optional[str]
    origin_name: Optional[str]
    reporter_name: Optional[str]
    driver_name: Optional[str]
    driver_role_name: Optional[str]
    vehicle_head_plate: Optional[str]
    vehicle_tail_plate: Optional[str]

    record_datetime: Optional[datetime]
    incident_datetime: Optional[datetime]

    province_name: Optional[str]
    district_name: Optional[str]
    sub_district_name: Optional[str]

    case_location: Optional[str]
    police_station_area: Optional[str]
    destination: Optional[str]

    truck_damage: Optional[str]
    truck_damage_details: Optional[str]
    product_damage: Optional[str]
    product_damage_details: Optional[str]
    case_details: Optional[str]

    injured_not_hospitalized: Optional[int]
    injured_hospitalized: Optional[int]
    fatalities: Optional[int]
    injury_description: Optional[str]

    other_party_full_name: Optional[str]
    other_party_vehicle_plate: Optional[str]
    other_party_company_name: Optional[str]
    other_party_phone: Optional[str]
    other_party_insurance_name: Optional[str]
    other_party_claim_no: Optional[str]
    claim_officer_full_name: Optional[str]
    claim_officer_phone: Optional[str]

    estimated_goods_damage_value: Optional[float]
    estimated_vehicle_damage_value: Optional[float]
    actual_goods_damage_value: Optional[float]
    actual_vehicle_damage_value: Optional[float]

    alcohol_test: Optional[str]
    alcohol_test_result: Optional[float]
    drug_test: Optional[str]
    drug_test_result: Optional[str]

    attachments: Optional[str]
    casestatus: Optional[str]
    priority: Optional[str]

    docs: List[AccidentCaseDocSchema] = []

    class Config:
        orm_mode = True


# ============================================================
# PROVINCE / DISTRICT / SUBDISTRICT
# ============================================================
class ProvinceBase(BaseModel):
    province_name_th: str
    province_name_en: Optional[str] = None

class ProvinceCreate(ProvinceBase): pass
class ProvinceUpdate(ProvinceBase): pass

class ProvinceResponse(ProvinceBase):
    province_id: int
    class Config: orm_mode = True


class DistrictBase(BaseModel):
    district_name_th: str
    district_name_en: Optional[str] = None
    province_id: int

class DistrictCreate(DistrictBase): pass
class DistrictUpdate(DistrictBase): pass

class DistrictResponse(DistrictBase):
    district_id: int
    province: Optional[ProvinceResponse] = None
    class Config: orm_mode = True


class SubDistrictBase(BaseModel):
    sub_district_name_th: str
    sub_district_name_en: Optional[str] = None
    district_id: int

class SubDistrictCreate(SubDistrictBase): pass
class SubDistrictUpdate(SubDistrictBase): pass

class SubDistrictResponse(SubDistrictBase):
    sub_district_id: int
    district: DistrictResponse
    class Config: orm_mode = True


# ============================================================
# LOCATION
# ============================================================
class LocationBase(BaseModel):
    location_name: str
    site_id: Optional[int] = None

class LocationResponse(LocationBase):
    location_id: int
    class Config:
        orm_mode = True


# ============================================================
# CASE REPORT INVESTIGATE + ACTIONS
# ============================================================
class CorrectiveActionItem(BaseModel):
    id: Optional[int] = None
    corrective_action: Optional[str] = None
    pic_contract: Optional[str] = None
    plan_date: Optional[date] = None
    action_completed_date: Optional[date] = None

    class Config:
        orm_mode = True


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
        orm_mode = True
