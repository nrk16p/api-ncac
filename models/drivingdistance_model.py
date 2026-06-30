from sqlalchemy import Column, Integer, String, Date, TIMESTAMP, func, Numeric
from database import Base


class DrivingDistance(Base):
    __tablename__ = "drivingdistance"

    id = Column(Integer, primary_key=True, index=True)
    plate_number = Column(String(50))
    truck_number = Column(String(50))
    gps_vendor = Column(String(50))
    date = Column(Date)
    distance = Column(Numeric(10, 2))
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
