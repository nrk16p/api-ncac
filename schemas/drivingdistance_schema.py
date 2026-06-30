from pydantic import BaseModel, field_validator
from datetime import date, datetime, timezone, timedelta
from typing import Optional

BKK_TZ = timezone(timedelta(hours=7))


class DrivingDistanceBase(BaseModel):
    plate_number: str
    truck_number: str
    gps_vendor: str
    date: date
    distance: float
    created_at: Optional[datetime] = None

    @field_validator("created_at", mode="before")
    @classmethod
    def convert_to_bangkok(cls, value):
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(BKK_TZ)
        return value

    class Config:
        from_attributes = True


class DrivingDistanceOut(DrivingDistanceBase):
    id: int


class DrivingDistanceCreate(DrivingDistanceBase):
    pass
