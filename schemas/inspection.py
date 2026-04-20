from pydantic import BaseModel
from typing import Optional, List, Dict
from datetime import date, datetime

# ---------------- TASK ----------------
class InspectionTaskCreate(BaseModel):
    trainer_id: Optional[str] = None
    client_name: Optional[str] = None
    plant_code: Optional[str] = None
    plant_name: Optional[str] = None   # 👈 เพิ่มตัวนี้
    plan_date:  Optional[date]= None 
    action_date: Optional[date]= None 
    inspection_task_status: str




# ---------------- DRIVER ----------------
class DriverCreate(BaseModel):

    driver_id: str
    number_plate: str
    truck_number: str
    truck_type: str


class ChecklistItem(BaseModel):
    item: str
    status: str  # PASS / FAIL / 
    fieldKey:str
    remark: Optional[str] = None


class VehicleInspectBase(BaseModel):
    checklist: Dict[str, List[ChecklistItem]]

    around_check_attachment: Optional[List[str]] = None
    cockpit_attachment: Optional[str] = None


class VehicleInspectCreate(VehicleInspectBase):
    pass


class VehicleInspectUpdate(BaseModel):
    checklist: Optional[Dict[str, List[ChecklistItem]]] = None
    around_check_attachment: Optional[List[str]] = None
    cockpit_attachment: Optional[str] = None


class VehicleInspectResponse(VehicleInspectBase):
    vehicle_inspect_id: int
    vechicle_status: str

    class Config:
        orm_mode = True

class InspectionTaskUpdate(BaseModel):
    trainer_id: Optional[str] = None
    client_name: Optional[str] = None
    action_date: Optional[date] = None
    inspection_task_status: Optional[str] = None
    drug_test_attachment: Optional[str] = None

class DrugTestCreate(BaseModel):
    alcohol: Optional[float] = None
    alcohol_attachment: Optional[str] = None

    amfetamin: Optional[str] = None
    amfetamin_attachment: Optional[str] = None

    kra: Optional[str] = None
    kra_attachment: Optional[str] = None

    thc: Optional[str] = None
    thc_attachment: Optional[str] = None


class DrugTestResponse(DrugTestCreate):
    drug_test_id: int

    # ✅ เพิ่มตรงนี้
    drug_test_status: str

    class Config:
        orm_mode = True

class AttendanceItem(BaseModel):
    driver_id: str
    first_name: str
    last_name: str
    status: str  # "เข้าร่วม" / "ไม่เข้าร่วม"
    date: Optional[date]


class SafetyTalkBase(BaseModel):
    topics: Optional[List[str]] = None
    attendance: Optional[List[AttendanceItem]] = None
    noted: Optional[str] = None
    upload_url: Optional[str] = None


class SafetyTalkCreate(SafetyTalkBase):
    pass


class SafetyTalkUpdate(SafetyTalkBase):
    pass


class SafetyTalkResponse(SafetyTalkBase):
    safety_talk_id: int
    inspection_task_id: str

    class Config:
        orm_mode = True

class DriverUpdate(BaseModel):
    number_plate: Optional[str] = None
    truck_number: Optional[str] = None
    truck_type: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None

    # ✅ NEW
    inspection_date: Optional[datetime] = None



# ---------------- PPE ----------------
class PPETestBase(BaseModel):


    helmet_check: Optional[str] = None
    glasses_check: Optional[str] = None
    mask_check: Optional[str] = None
    vest_check: Optional[str] = None              # ✅ ADD
    glove_check: Optional[str] = None
    safety_shoes_check: Optional[str] = None      # ✅ ADD
    vest_size: Optional[str] = None 
    safety_shoes_size: Optional[str] = None 
    ppe_attachment: Optional[str] = None

class PPETestCreate(PPETestBase):
    pass


class PPETestUpdate(PPETestBase):
    pass


class PPETestResponse(BaseModel):
    ppe_test_id: int
    helmet_check: Optional[str]
    glasses_check: Optional[str]
    mask_check: Optional[str]
    vest_check: Optional[str]
    glove_check: Optional[str]
    safety_shoes_check: Optional[str]
    vest_size: Optional[str] = None 
    safety_shoes_size: Optional[str] = None 
    ppe_attachment: Optional[str]

    ppe_status: str   # ✅ ต้องมี

    class Config:
        orm_mode = True


class DeleteTaskRequest(BaseModel):
    deleted_by: str
    remark: str | None = None