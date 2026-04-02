from sqlalchemy import Column, String
from database import Base

class PlantMaster(Base):
    __tablename__ = "plant_master"

    plant_code = Column(String(50), primary_key=True)
    fleet = Column(String(100), primary_key=True)

    plant_name = Column(String(100))