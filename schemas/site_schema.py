from pydantic import BaseModel
from typing import Optional

class SiteBase(BaseModel):
    site_code: Optional[str] = None
    site_name_th: str
    site_name_en: str

class SiteCreate(SiteBase):
    pass

class SiteResponse(SiteBase):
    site_id: int
    class Config:
        from_attributes = True
