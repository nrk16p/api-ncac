from pydantic import BaseModel
from datetime import datetime, date


class MixerCompensationCreate(BaseModel):

    TicketNo: str
    TruckNo: str
    TruckPlateNo: str
    TruckPlateNo_clean: str

    PlantName: str

    SiteMoveInAt: datetime | None
    SiteMoveOutAt: datetime | None

    minutes_diff: int | None

    tier: str
    truck_type: str
    compensate: float

    TicketCreateAt: datetime | None

    date_ticket: date

    is_complete_trip: str


class MixerCompensationResponse(MixerCompensationCreate):

    id: int

    class Config:
        from_attributes = True