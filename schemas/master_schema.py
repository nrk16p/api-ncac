from pydantic import BaseModel
from typing import Optional


class MasterDriverResponse(BaseModel):
    driver_id: int
    first_name: Optional[str]
    last_name: Optional[str]
    site_id: Optional[int]
    driver_role_id: Optional[int]

    class Config:
        orm_mode = True


class VehicleResponse(BaseModel):
    vehicle_id: int
    truck_no: str
    vehicle_number_plate: str
    plate_type: str

    class Config:
        orm_mode = True
