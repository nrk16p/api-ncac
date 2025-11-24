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

    injured_not_hospitalized = Column(Integer, default=0)
    injured_hospitalized = Column(Integer, default=0)
    fatalities = Column(Integer, default=0)

    attachments = Column(String(500))
    casestatus = Column(String(500))
    priority = Column(String(500))

    # Relationships
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
            "site_name": self.site.site_name_th if self.site else None,
            "department_name": self.department.department_name_th if self.department else None,
            "client_name": self.client.client_name if self.client else None,
            "origin_name": self.origin.location_name if self.origin else None,
            "reporter_name": f"{self.reporter.firstname} {self.reporter.lastname}" if self.reporter else None,
            "driver_name": f"{self.driver.first_name} {self.driver.last_name}" if self.driver else None,
            "driver_role_name": self.driver_role.role_name if self.driver_role else None,
            "vehicle_head_plate": self.vehicle_head.vehicle_number_plate if self.vehicle_head else None,
            "vehicle_tail_plate": self.vehicle_tail.vehicle_number_plate if self.vehicle_tail else None,
            "record_datetime": self.record_datetime,
            "incident_datetime": self.incident_datetime,
            "province_name": self.province.province_name_th if self.province else None,
            "district_name": self.district.district_name_th if self.district else None,
            "sub_district_name": self.sub_district.sub_district_name_th if self.sub_district else None,
            "case_location": self.case_location,
            "police_station_area": self.police_station_area,
            "destination": self.destination,
            "truck_damage": None,
            "product_damage": None,
            "case_details": self.case_details,
            "estimated_goods_damage_value": self.estimated_goods_damage_value,
            "estimated_vehicle_damage_value": self.estimated_vehicle_damage_value,
            "actual_goods_damage_value": self.actual_goods_damage_value,
            "actual_vehicle_damage_value": self.actual_vehicle_damage_value,
            "alcohol_test_result": self.alcohol_test_result,
            "drug_test_result": self.drug_test_result,
            "attachments": self.attachments,
            "casestatus": self.casestatus,
            "priority": self.priority,
            "docs": [
                {
                    "id": d.id,
                    "document_no_ac": d.document_no_ac,
                    "data": d.data,
                    "created_at": d.created_at,
                    "updated_at": d.updated_at,
                }
                for d in self.docs
            ],
        }


class AccidentCaseDoc(Base):
    __tablename__ = "accident_case_docs"

    id = Column(Integer, primary_key=True)
    document_no_ac = Column(String(50), ForeignKey("accident_cases.document_no_ac", ondelete="CASCADE"))
    data = Column(JSONB, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    accident_case = relationship("AccidentCase", back_populates="docs")
