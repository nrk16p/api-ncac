from pydantic import BaseModel
from typing import Optional

# Province
class ProvinceBase(BaseModel):
    province_name_th: str
    province_name_en: Optional[str] = None

class ProvinceCreate(ProvinceBase):
    pass

class ProvinceResponse(ProvinceBase):
    province_id: int
    class Config:
        from_attributes = True

# District
class DistrictBase(BaseModel):
    district_name_th: str
    district_name_en: Optional[str] = None
    province_id: int

class DistrictCreate(DistrictBase):
    pass

class DistrictResponse(DistrictBase):
    district_id: int
    class Config:
        from_attributes = True

# SubDistrict
class SubDistrictBase(BaseModel):
    sub_district_name_th: str
    sub_district_name_en: Optional[str] = None
    district_id: int

class SubDistrictCreate(SubDistrictBase):
    pass

class SubDistrictResponse(SubDistrictBase):
    sub_district_id: int
    class Config:
        from_attributes = True

# Location
class LocationBase(BaseModel):
    location_name: str
    site_id: Optional[int] = None

class LocationResponse(LocationBase):
    location_id: int
    class Config:
        from_attributes = True
# Province
class ProvinceUpdate(ProvinceBase):
    pass

# District
class DistrictUpdate(DistrictBase):
    pass

# SubDistrict
class SubDistrictUpdate(SubDistrictBase):
    pass
