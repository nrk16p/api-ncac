from pydantic import BaseModel
from typing import Optional


class PositionLevelBase(BaseModel):
    level_name: str


class PositionLevelResponse(PositionLevelBase):
    position_level_id: int

    class Config:
        from_attributes = True


class PositionBase(BaseModel):
    position_name_th: str
    position_name_en: str
    position_level_id: int


class Position(PositionBase):
    pass


class PositionResponse(PositionBase):
    position_id: int
    level: Optional[PositionLevelResponse] = None

    class Config:
        from_attributes = True
