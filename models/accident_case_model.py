from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from database import Base

class AccidentCase(Base):
    __tablename__ = "accident_cases"

    accident_case_id = Column(Integer, primary_key=True, index=True)
    document_no_ac = Column(String(50), unique=True, nullable=False, index=True)

    site_id = Column(Integer, ForeignKey("sites.site_id"))
    department_id = Column(Integer, ForeignKey("departments.department_id"))
    client_id = Column(Integer, ForeignKey("clients.client_id"))
    origin_id = Column(Integer, ForeignKey("locations.location_id"))
    reporter_id = Column(Integer, ForeignKey("users.id"))
    driver_id = Column(String(255), ForeignKey("masterdrivers.driver_id"))
    driver_role_id = Column(Integer, ForeignKey("driver_roles.driver_role_id"))
    vehicle_id_head = Column(Integer, ForeignKey("vehicles.vehicle_id"))
    vehicle_id_tail = Column(Integer, ForeignKey("vehicles.vehicle_id"))
    province_id = Column(Integer, ForeignKey("provinces.province_id"))
    district_id = Column(Integer, ForeignKey("districts.district_id"))
    sub_district_id = Column(Integer, ForeignKey("sub_districts.sub_district_id"))

    record_datetime = Column(DateTime, default=datetime.utcnow)
    incident_datetime = Column(DateTime)
    destination = Column(String(255))
    case_location = Column(String(255))
    police_station_area = Column(String(255))
    vehicle_truckno = Column(String(50))
    case_details = Column(String(500))

    alcohol_test_result = Column(Float)
    drug_test_result = Column(String(500))

    estimated_goods_damage_value = Column(Float)
    estimated_vehicle_damage_value = Column(Float)
    actual_goods_damage_value = Column(Float)
    actual_vehicle_damage_value = Column(Float)

    # ✅ These must exist for your JSON payload
    injured_not_hospitalized = Column(Integer, default=0)
    injured_hospitalized = Column(Integer, default=0)
    fatalities = Column(Integer, default=0)

    attachments = Column(String(500))
    casestatus = Column(String(500))
    priority = Column(String(500))

    # ✅ Relationships
    site = relationship("Site")
    department = relationship("Department")
    client = relationship("Client")
    origin = relationship("Location")
    reporter = relationship("User")
    driver = relationship("MasterDriver")
    driver_role = relationship("DriverRole")
    vehicle_head = relationship("Vehicle", foreign_keys=[vehicle_id_head])
    vehicle_tail = relationship("Vehicle", foreign_keys=[vehicle_id_tail])
    province = relationship("Province")
    district = relationship("District")
    sub_district = relationship("SubDistrict")

    docs = relationship(
        "AccidentCaseDoc",
        back_populates="accident_case",
        cascade="all, delete-orphan"
    )

    def to_dict(self):
        return {
            "accident_case_id": self.accident_case_id,
            "document_no_ac": self.document_no_ac,
            "vehicle_truckno": self.vehicle_truckno,
            "case_location": self.case_location,
            "case_details": self.case_details,
            "injured_not_hospitalized": self.injured_not_hospitalized,
            "injured_hospitalized": self.injured_hospitalized,
            "fatalities": self.fatalities,
            "attachments": self.attachments,
            "casestatus": self.casestatus,
            "priority": self.priority,
            "docs": [doc.data for doc in self.docs],
        }


class AccidentCaseDoc(Base):
    __tablename__ = "accident_case_docs"

    id = Column(Integer, primary_key=True)
    document_no_ac = Column(String(50), ForeignKey("accident_cases.document_no_ac", ondelete="CASCADE"))
    data = Column(JSONB, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    accident_case = relationship("AccidentCase", back_populates="docs")
