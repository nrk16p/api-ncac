from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from database import Base

from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    Text,
    Numeric,
    ForeignKey,
)
from sqlalchemy.orm import relationship
from database import Base


class AccidentCase(Base):
    __tablename__ = "accident_cases"

    accident_case_id = Column(Integer, primary_key=True, index=True)
    document_no_ac = Column(String(50), unique=True, nullable=False, index=True)

    # --- Foreign Keys ---
    site_id = Column(Integer, ForeignKey("sites.site_id"))
    department_id = Column(Integer, ForeignKey("departments.department_id"))
    client_id = Column(Integer, ForeignKey("clients.client_id"))
    origin_id = Column(Integer, ForeignKey("locations.location_id"))
    reporter_id = Column(Integer, ForeignKey("users.id"))
    driver_id = Column(Text, ForeignKey("masterdrivers.driver_id"))
    driver_role_id = Column(Integer, ForeignKey("driver_roles.driver_role_id"))
    vehicle_id_head = Column(Integer, ForeignKey("vehicles.vehicle_id"))
    vehicle_id_tail = Column(Integer, ForeignKey("vehicles.vehicle_id"))
    province_id = Column(Integer, ForeignKey("provinces.province_id"))
    district_id = Column(Integer, ForeignKey("districts.district_id"))
    sub_district_id = Column(Integer, ForeignKey("sub_districts.sub_district_id"))

    # --- Core Information ---
    record_datetime = Column(DateTime(timezone=True), default=datetime.utcnow)
    incident_datetime = Column(DateTime(timezone=True))
    destination = Column(String(255))
    case_location = Column(String(255))
    police_station_area = Column(String(255))
    vehicle_truckno = Column(String(50))
    case_details = Column(Text)

    # --- Tests ---
    alcohol_test = Column(Text)
    drug_test = Column(Text)
    alcohol_test_result = Column(Float)
    drug_test_result = Column(String(255))
    fault_party = Column(String(255))

    # --- Damage Information ---
    truck_damage = Column(Text)
    truck_damage_details = Column(Text)
    product_damage = Column(Text)
    product_damage_details = Column(Text)
    estimated_goods_damage_value = Column(Numeric(12, 2))
    estimated_vehicle_damage_value = Column(Numeric(12, 2))
    actual_goods_damage_value = Column(Numeric(12, 2))
    actual_vehicle_damage_value = Column(Numeric(12, 2))

    # --- Injury Information ---
    injured_not_hospitalized = Column(Integer, default=0)
    injured_hospitalized = Column(Integer, default=0)
    fatalities = Column(Integer, default=0)
    injury_description = Column(Text)

    # --- Other Party Information ---
    other_party_full_name = Column(String(255))
    other_party_vehicle_plate = Column(String(50))
    other_party_company_name = Column(String(255))
    other_party_phone = Column(String(20))
    other_party_insurance_name = Column(String(255))
    other_party_claim_no = Column(String(50))

    # --- Claim Officer Information ---
    claim_officer_full_name = Column(String(255))
    claim_officer_phone = Column(String(20))

    # --- Other Info ---
    attachments = Column(Text)
    casestatus = Column(String(255))
    priority = Column(String(255))

    # --- Relationships ---
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

    # --- Serializer ---
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
            "vehicle_truckno": self.vehicle_truckno,
            "case_details": self.case_details,
            "alcohol_test": self.alcohol_test,
            "drug_test": self.drug_test,
            "alcohol_test_result": self.alcohol_test_result,
            "drug_test_result": self.drug_test_result,
            "truck_damage": self.truck_damage,
            "truck_damage_details": self.truck_damage_details,
            "fault_party": self.fault_party,
            "product_damage": self.product_damage,
            "product_damage_details": self.product_damage_details,
            "estimated_goods_damage_value": self.estimated_goods_damage_value,
            "estimated_vehicle_damage_value": self.estimated_vehicle_damage_value,
            "actual_goods_damage_value": self.actual_goods_damage_value,
            "actual_vehicle_damage_value": self.actual_vehicle_damage_value,
            "injured_not_hospitalized": self.injured_not_hospitalized,
            "injured_hospitalized": self.injured_hospitalized,
            "fatalities": self.fatalities,
            "injury_description": self.injury_description,
            "other_party_full_name": self.other_party_full_name,
            "other_party_vehicle_plate": self.other_party_vehicle_plate,
            "other_party_company_name": self.other_party_company_name,
            "other_party_phone": self.other_party_phone,
            "other_party_insurance_name": self.other_party_insurance_name,
            "other_party_claim_no": self.other_party_claim_no,
            "claim_officer_full_name": self.claim_officer_full_name,
            "claim_officer_phone": self.claim_officer_phone,
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
