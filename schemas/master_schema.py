from pydantic import BaseModel
from typing import Optional

# ===================== DRIVER ROLE =====================
class DriverRoleBase(BaseModel):
    role_name: str

class DriverRoleCreate(DriverRoleBase):
    pass

class DriverRoleResponse(DriverRoleBase):
    driver_role_id: int
    class Config:
        from_attributes = True


# ===================== MASTER DRIVER =====================
class MasterDriverBase(BaseModel):
    first_name: str
    last_name: str
    site_id: Optional[int] = None
    driver_role_id: Optional[int] = None

class MasterDriverCreate(MasterDriverBase):
    pass

class MasterDriverResponse(MasterDriverBase):
    driver_id: int
    class Config:
        from_attributes = True


# ===================== MASTER CAUSE =====================
class MasterCauseBase(BaseModel):
    cause_name: str
    description: Optional[str] = None

class MasterCauseCreate(MasterCauseBase):
    pass

class MasterCauseResponse(MasterCauseBase):
    cause_id: int
    class Config:
        from_attributes = True


# ===================== VEHICLE =====================
class VehicleBase(BaseModel):
    truck_no: str
    vehicle_number_plate: str
    plate_type: str

class VehicleCreate(VehicleBase):
    pass

class VehicleResponse(VehicleBase):
    vehicle_id: int
    class Config:
        from_attributes = True
