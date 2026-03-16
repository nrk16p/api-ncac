from sqlalchemy import Column, Integer, String, BigInteger, Numeric, Date, DateTime
from database import Base


class MixerCompensationCPAC(Base):

    __tablename__ = "mixer_compensation_cpac"
    __table_args__ = {"schema": "fuel"}

    id = Column(Integer, primary_key=True, index=True)

    TicketNo = Column(String)
    TruckNo = Column(String)
    TruckPlateNo = Column(String)
    TruckPlateNo_clean = Column(String)

    PlantName = Column(String)

    SiteMoveInAt = Column(DateTime)
    SiteMoveOutAt = Column(DateTime)

    minutes_diff = Column(BigInteger)

    tier = Column(String)

    truck_type = Column(String)

    compensate = Column(Numeric)

    TicketCreateAt = Column(DateTime)

    date_ticket = Column(Date)

    is_complete_trip = Column(String)