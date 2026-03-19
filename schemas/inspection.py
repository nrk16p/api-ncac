from pydantic import BaseModel
from typing import Optional, List
from datetime import date, datetime

# ---------------- TASK ----------------
class InspectionTaskCreate(BaseModel):

    trainer_id: str
    client_name: str
    plant_code: str
    plant_name: str

    plan_date: date
    action_date: Optional[datetime] = None

    inspection_task_status: Optional[str] = None


class InspectionTaskUpdate(BaseModel):
    trainer_id: Optional[int]
    client_id: Optional[int]
    plant_id: Optional[int]
    inspection_task_status: Optional[str]


# ---------------- DRIVER ----------------
class DriverCreate(BaseModel):

    driver_id: str
    truck_id: str
    truck_number: str
    truck_type: str


# ---------------- DRUG TEST ----------------
class DrugTestCreate(BaseModel):
    alcohol: Optional[float] = None
    alcohol_attachment: Optional[str] = None
    amfetamin_2: Optional[str] = None
    amfetamin_attachment: Optional[str] = None


# ---------------- PPE ----------------
class PPETestCreate(BaseModel):
    shirt_check: Optional[str] = None
    shirt_size: Optional[str] = None
    boot_check: Optional[str] = None
    boot_size: Optional[str] = None
    ppe_attachment: Optional[str] = None


class VehicleInspectCreate(BaseModel):

    around_check_side_1: Optional[str] = None
    around_check_side_2: Optional[str] = None
    around_check_side_3: Optional[str] = None
    around_check_side_4: Optional[str] = None

    around_check_attachment: Optional[List[str]] = None

    equiement_check: Optional[str] = None
    cockpit_check: Optional[str] = None
    cockpit_check_attachment: Optional[str] = None