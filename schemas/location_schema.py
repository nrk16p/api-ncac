from pydantic import BaseModel
from typing import Optional


class ProvinceResponse(BaseModel):
    province_id: int
    province_name_th: str

    class Config:
        orm_mode = True


class DistrictResponse(BaseModel):
    district_id: int
    district_name_th: str
    province_id: int

    class Config:
        orm_mode = True


class SubDistrictResponse(BaseModel):
    sub_district_id: int
    sub_district_name_th: str
    district_id: int

    class Config:
        orm_mode = True
