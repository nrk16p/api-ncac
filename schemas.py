from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

# -----------------
# Register
# -----------------
class RegisterRequest(BaseModel):
    username: str
    password: str
    firstname: str
    lastname: str
    employee_id: Optional[str] = None
    department_id: Optional[int] = None
    site_id: Optional[int] = None
    position_id: Optional[int] = None

# -----------------
# Position Level
# -----------------
class PositionLevelBase(BaseModel):
    level_name: str

class PositionLevelCreate(PositionLevelBase):
    pass

class PositionLevel(PositionLevelBase):
    position_level_id: int

    class Config:
        orm_mode = True

# -----------------
# Position
# -----------------
class PositionBase(BaseModel):
    position_name_th: str
    position_name_en: str
    position_level_id: Optional[int]

class PositionCreate(PositionBase):
    pass

class Position(PositionBase):
    position_id: int
    # 👇 ใช้ level แทน position_level (ตรงกับ models.Position.level)
    level: Optional[PositionLevel] = None

    class Config:
        orm_mode = True

# -----------------
# User
# -----------------
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
    # ถ้าอยาก return relation กำหนดเพิ่มได้ เช่น:
    position: Optional[Position] = None

    class Config:
        orm_mode = True
from pydantic import BaseModel
from datetime import datetime
from typing import Optional

# ✅ Base: shared optional fields (no document_no_ac here)
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



# ✅ Create: what the client sends (no accident_case_id, no document_no_ac)
class AccidentCaseCreate(AccidentCaseBase):
    pass


# ✅ Update: same as base, partial updates allowed
class AccidentCaseUpdate(AccidentCaseBase):
    pass


# ✅ Response: includes system-generated fields
class AccidentCaseResponse(AccidentCaseBase):
    accident_case_id: int
    document_no_ac: str   # system-generated

    class Config:
        orm_mode = True
        
# ----------------------
# Province
# ----------------------
class ProvinceBase(BaseModel):
    province_name_th: str
    province_name_en: Optional[str] = None


class ProvinceCreate(ProvinceBase):
    pass


class ProvinceUpdate(ProvinceBase):
    pass


class ProvinceResponse(ProvinceBase):
    province_id: int

    class Config:
        orm_mode = True


# ----------------------
# District
# ----------------------
class DistrictBase(BaseModel):
    district_name_th: str
    district_name_en: Optional[str] = None
    province_id: int


class DistrictCreate(DistrictBase):
    pass


class DistrictUpdate(DistrictBase):
    pass


class DistrictResponse(DistrictBase):
    district_id: int
    province: Optional[ProvinceResponse] = None   # ✅ allow missing

    class Config:
        orm_mode = True


# ----------------------
# SubDistrict
# ----------------------
class SubDistrictBase(BaseModel):
    sub_district_name_th: str
    sub_district_name_en: Optional[str] = None
    district_id: int


class SubDistrictCreate(SubDistrictBase):
    pass


class SubDistrictUpdate(SubDistrictBase):
    pass


class SubDistrictResponse(SubDistrictBase):
    sub_district_id: int
    district: DistrictResponse

    class Config:
        orm_mode = True
class LocationBase(BaseModel):
    location_name: str
    site_id: Optional[int] = None
            
class LocationResponse(LocationBase):
    location_id: int

    class Config:
        orm_mode = True