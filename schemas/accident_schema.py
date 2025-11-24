from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

# ============================================================
# ACCIDENT CASE DOC DATA
# ============================================================
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

    # 🆕 Extended fields
    account_attachment_sold: Optional[str] = None
    account_attachment_sold_no: Optional[str] = None
    account_attachment_sold_remark: Optional[str] = None
    account_attachment_insurance: Optional[str] = None
    account_attachment_insurance_no: Optional[str] = None
    account_attachment_insurance_remark: Optional[str] = None
    account_attachment_driver: Optional[str] = None
    account_attachment_driver_no: Optional[str] = None
    account_attachment_driver_remark: Optional[str] = None
    account_attachment_company: Optional[str] = None
    account_attachment_company_no: Optional[str] = None
    account_attachment_company_remark: Optional[str] = None


# ============================================================
# ACCIDENT CASE DOC SCHEMA
# ============================================================
class AccidentCaseDocSchema(AccidentCaseDocData):
    class Config:
        orm_mode = True


# ============================================================
# ACCIDENT CASE CREATE / UPDATE / RESPONSE
# ============================================================
class AccidentCaseCreate(BaseModel):
    site_id: int
    department_id: int
    client_id: Optional[int]
    origin_id: Optional[int]
    reporter_id: Optional[int]
    driver_id: Optional[str]
    driver_role_id: Optional[int]
    vehicle_id_head: Optional[int]
    vehicle_id_tail: Optional[int]
    province_id: Optional[int]
    district_id: Optional[int]
    sub_district_id: Optional[int]
    record_datetime: datetime
    incident_datetime: datetime
    case_location: Optional[str]
    destination: Optional[str]
    case_details: Optional[str]
    estimated_goods_damage_value: Optional[float]
    estimated_vehicle_damage_value: Optional[float]
    actual_goods_damage_value: Optional[float]
    actual_vehicle_damage_value: Optional[float]
    alcohol_test_result: Optional[float]
    drug_test_result: Optional[str]
    injured_not_hospitalized: Optional[int]
    injured_hospitalized: Optional[int]
    fatalities: Optional[int]
    docs: Optional[List[AccidentCaseDocSchema]] = []  # keep list form


class AccidentCaseUpdate(BaseModel):
    case_location: Optional[str]
    destination: Optional[str]
    case_details: Optional[str]
    casestatus: Optional[str]
    priority: Optional[str]


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
    product_damage: Optional[str]
    case_details: Optional[str]
    estimated_goods_damage_value: Optional[float]
    estimated_vehicle_damage_value: Optional[float]
    actual_goods_damage_value: Optional[float]
    actual_vehicle_damage_value: Optional[float]
    alcohol_test_result: Optional[float]
    drug_test_result: Optional[str]
    attachments: Optional[str]
    casestatus: Optional[str]
    priority: Optional[str]
    docs: List[AccidentCaseDocSchema] = []

    class Config:
        orm_mode = True
