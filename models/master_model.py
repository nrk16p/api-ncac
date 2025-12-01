from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from database import Base


class DriverRole(Base):
    __tablename__ = "driver_roles"
    driver_role_id = Column(Integer, primary_key=True, index=True)
    role_name = Column(String(100), nullable=False)


class MasterDriver(Base):
    __tablename__ = "masterdrivers"
    driver_id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String(100))
    last_name = Column(String(100))
    site_id = Column(Integer, ForeignKey("sites.site_id"))
    driver_role_id = Column(Integer, ForeignKey("driver_roles.driver_role_id"))

    site = relationship("Site", backref="drivers")
    role = relationship("DriverRole", backref="drivers")


class MasterCause(Base):
    __tablename__ = "mastercauses"
    cause_id = Column(Integer, primary_key=True, index=True)
    cause_name = Column(String(255), nullable=False)
    description = Column(String(255))


class Vehicle(Base):
    __tablename__ = "vehicles"
    vehicle_id = Column(Integer, primary_key=True, index=True)
    truck_no = Column(String(50), nullable=False)
    vehicle_number_plate = Column(String(50), nullable=False)
    plate_type = Column(String(50), nullable=False)




