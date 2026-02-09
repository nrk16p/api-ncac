from pydantic import BaseModel, field_validator
from typing import List, Optional, Any
from datetime import datetime

# ============================================================
# ACCIDENT CASE DOC DATA (individual doc fields)
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

    # Extended attachments
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
# ACCIDENT CASE DOC SCHEMA (database representation)
# ============================================================
class AccidentCaseDocSchema(BaseModel):
    id: Optional[int] = None
    document_no_ac: Optional[str] = None
    data: dict
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True  # ✅ updated for Pydantic v2


# ============================================================
# ACCIDENT CASE CREATE / UPDATE / RESPONSE
# ============================================================
class AccidentCaseCreate(BaseModel):
    # 🔹 Basic & Foreign Keys
    document_no_ac: str = None
    site_id: int
    department_id: int
    client_id: Optional[int] = None
    origin_id: Optional[int] = None
    reporter_id: Optional[int] = None
    driver_id: Optional[str] = None
    driver_role_id: Optional[int] = None
    vehicle_id_head: Optional[int] = None
    vehicle_id_tail: Optional[int] = None
    vehicle_truckno: Optional[str] = None

    # 🔹 Date & Location
    record_datetime: datetime
    incident_datetime: datetime
    province_id: Optional[int] = None
    district_id: Optional[int] = None
    sub_district_id: Optional[int] = None
    case_location: Optional[str] = None
    police_station_area: Optional[str] = None
    destination: Optional[str] = None

    # 🔹 Case & Damage Info
    case_details: Optional[str] = None
    truck_damage: Optional[str] = None
    truck_damage_details: Optional[str] = None
    product_damage: Optional[str] = None
    product_damage_details: Optional[str] = None
    fault_party:Optional[str] = None


    # 🔹 Test Results
    alcohol_test: Optional[str] = None
    alcohol_test_result: Optional[float] = None
    drug_test: Optional[str] = None
    drug_test_result: Optional[str] = None

    # 🔹 Damage Values
    estimated_goods_damage_value: Optional[float] = None
    estimated_vehicle_damage_value: Optional[float] = None
    actual_goods_damage_value: Optional[float] = None
    actual_vehicle_damage_value: Optional[float] = None

    # 🔹 Injury Info
    injured_not_hospitalized: Optional[int] = None
    injured_hospitalized: Optional[int] = None
    fatalities: Optional[int] = None
    injury_description: Optional[str] = None

    # 🔹 Other Party Info
    other_party_full_name: Optional[str] = None
    other_party_vehicle_plate: Optional[str] = None
    other_party_company_name: Optional[str] = None
    other_party_phone: Optional[str] = None
    other_party_insurance_name: Optional[str] = None
    other_party_claim_no: Optional[str] = None
    claim_officer_full_name: Optional[str] = None
    claim_officer_phone: Optional[str] = None

    # 🔹 Attachments & Status v
    attachments: Optional[str] = None
    casestatus: Optional[str] = None
    priority: Optional[str] = None

    # 🔹 Documents
    docs: Optional[List[dict]] = None

    @field_validator("docs", mode="before")
    @classmethod
    def ensure_list(cls, v):
        if isinstance(v, dict):
            return [v]
        return v

    class Config:
        from_attributes = True  # ✅ Pydantic v2 compatible

class AccidentCaseUpdate(BaseModel):
    # 🔹 Basic & Foreign Keys
    site_id: Optional[int] = None
    department_id: Optional[int] = None
    client_id: Optional[int] = None
    origin_id: Optional[int] = None
    reporter_id: Optional[int] = None
    driver_id: Optional[str] = None
    driver_role_id: Optional[int] = None
    vehicle_id_head: Optional[int] = None
    vehicle_id_tail: Optional[int] = None
    vehicle_truckno: Optional[str] = None

    # 🔹 Date & Location
    record_datetime: Optional[datetime] = None
    incident_datetime: Optional[datetime] = None
    province_id: Optional[int] = None
    district_id: Optional[int] = None
    sub_district_id: Optional[int] = None
    case_location: Optional[str] = None
    police_station_area: Optional[str] = None
    destination: Optional[str] = None

    # 🔹 Case & Damage Info
    case_details: Optional[str] = None
    truck_damage: Optional[str] = None
    truck_damage_details: Optional[str] = None
    product_damage: Optional[str] = None
    product_damage_details: Optional[str] = None

    # 🔹 Test Results
    alcohol_test: Optional[str] = None
    alcohol_test_result: Optional[float] = None
    drug_test: Optional[str] = None
    drug_test_result: Optional[str] = None

    # 🔹 Damage Values
    estimated_goods_damage_value: Optional[float] = None
    estimated_vehicle_damage_value: Optional[float] = None
    actual_goods_damage_value: Optional[float] = None
    actual_vehicle_damage_value: Optional[float] = None

    # 🔹 Injury Info
    injured_not_hospitalized: Optional[int] = None
    injured_hospitalized: Optional[int] = None
    fatalities: Optional[int] = None
    injury_description: Optional[str] = None

    # 🔹 Other Party Info
    other_party_full_name: Optional[str] = None
    other_party_vehicle_plate: Optional[str] = None
    other_party_company_name: Optional[str] = None
    other_party_phone: Optional[str] = None
    other_party_insurance_name: Optional[str] = None
    other_party_claim_no: Optional[str] = None
    claim_officer_full_name: Optional[str] = None
    claim_officer_phone: Optional[str] = None

    # 🔹 Attachments & Status
    attachments: Optional[str] = None
    casestatus: Optional[str] = None
    priority: Optional[str] = None

    # 🔹 Documents
    docs: Optional[List[dict]] = None

    @field_validator("docs", mode="before")
    @classmethod
    def ensure_list(cls, v):
        if isinstance(v, dict):
            return [v]
        return v

    class Config:
        from_attributes = True  # ✅ For Pydantic v2


class AccidentCaseResponse(BaseModel):
    accident_case_id: int
    document_no_ac: str

    # 🔹 Linked Entities
    site_name: Optional[str] = None
    department_name: Optional[str] = None
    client_name: Optional[str] = None
    origin_name: Optional[str] = None
    reporter_name: Optional[str] = None
    driver_name: Optional[str] = None
    driver_role_name: Optional[str] = None
    vehicle_head_plate: Optional[str] = None
    vehicle_tail_plate: Optional[str] = None

    # 🔹 Date & Location Info
    record_datetime: Optional[datetime] = None
    incident_datetime: Optional[datetime] = None
    province_name: Optional[str] = None
    district_name: Optional[str] = None
    sub_district_name: Optional[str] = None
    case_location: Optional[str] = None
    police_station_area: Optional[str] = None
    destination: Optional[str] = None

    # 🔹 Damage & Case Details
    truck_damage: Optional[str] = None
    truck_damage_details: Optional[str] = None
    product_damage: Optional[str] = None
    product_damage_details: Optional[str] = None
    case_details: Optional[str] = None

    # 🔹 Injury Details
    injured_not_hospitalized: Optional[int] = None
    injured_hospitalized: Optional[int] = None
    fatalities: Optional[int] = None
    injury_description: Optional[str] = None

    # 🔹 Other Party Info
    other_party_full_name: Optional[str] = None
    other_party_vehicle_plate: Optional[str] = None
    other_party_company_name: Optional[str] = None
    other_party_phone: Optional[str] = None
    other_party_insurance_name: Optional[str] = None
    other_party_claim_no: Optional[str] = None
    claim_officer_full_name: Optional[str] = None
    claim_officer_phone: Optional[str] = None
    fault_party:Optional[str] = None

    # 🔹 Test Results
    alcohol_test: Optional[str] = None
    alcohol_test_result: Optional[float] = None
    drug_test: Optional[str] = None
    drug_test_result: Optional[str] = None

    # 🔹 Cost Info
    estimated_goods_damage_value: Optional[float] = None
    estimated_vehicle_damage_value: Optional[float] = None
    actual_goods_damage_value: Optional[float] = None
    actual_vehicle_damage_value: Optional[float] = None
    fault_party :Optional[str] = None

    # 🔹 Attachments & Status
    attachments: Optional[str] = None
    casestatus: Optional[str] = None
    priority: Optional[str] = None

    # 🔹 Related Documents
    docs: Optional[List[dict[str, Any]]] = None

    class Config:
        from_attributes = True  # ✅ Pydantic v2 
