from pydantic import BaseModel
from typing import Optional


# ============================================================
# POSITION LEVEL
# ============================================================
class PositionLevelBase(BaseModel):
    level_name: str

class PositionLevelCreate(PositionLevelBase):
    pass

class PositionLevel(PositionLevelBase):
    position_level_id: int

    class Config:
        orm_mode = True


# ============================================================
# POSITION
# ============================================================
class PositionBase(BaseModel):
    position_name_th: str
    position_name_en: str
    position_level_id: Optional[int]

class PositionCreate(PositionBase):
    pass

class Position(PositionBase):
    position_id: int
    level: Optional[PositionLevel] = None
    class Config:
        orm_mode = True