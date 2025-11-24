from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from database import Base


class Province(Base):
    __tablename__ = "provinces"
    province_id = Column(Integer, primary_key=True, index=True)
    province_name_th = Column(String(255), nullable=False)
    province_name_en = Column(String(255))
    districts = relationship("District", back_populates="province")


class District(Base):
    __tablename__ = "districts"
    district_id = Column(Integer, primary_key=True, index=True)
    province_id = Column(Integer, ForeignKey("provinces.province_id"))
    district_name_th = Column(String(100), nullable=False)
    district_name_en = Column(String(100))
    province = relationship("Province", back_populates="districts")
    sub_districts = relationship("SubDistrict", back_populates="district")


class SubDistrict(Base):
    __tablename__ = "sub_districts"
    sub_district_id = Column(Integer, primary_key=True, index=True)
    district_id = Column(Integer, ForeignKey("districts.district_id"))
    sub_district_name_th = Column(String(100), nullable=False)
    sub_district_name_en = Column(String(100))
    district = relationship("District", back_populates="sub_districts")


class Location(Base):
    __tablename__ = "locations"
    location_id = Column(Integer, primary_key=True, index=True)
    location_name = Column(String(255), nullable=False)
    site_id = Column(Integer, ForeignKey("sites.site_id"))
