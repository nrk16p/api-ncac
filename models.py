from datetime import datetime
from typing import Optional
from sqlalchemy.dialects.postgresql import JSONB

from sqlalchemy import (
    Column,
    Integer,
    String,
    ForeignKey,
    DateTime,
    Text,
    Numeric,
    Date,
    TIMESTAMP,
    Float,
    func,
)
from sqlalchemy.orm import relationship, validates
from passlib.context import CryptContext
from pydantic import BaseModel

from database import Base

# ======================================================
# 🔐 Password hashing
# ======================================================
pwd_context = CryptContext(
    schemes=["argon2"],  # ✅ ใช้ argon2 แทน bcrypt
    deprecated="auto",
)


# ======================================================
# 🔹 Position / User / Org Master
# ======================================================
class PositionLevel(Base):
    __tablename__ = "position_levels"
    position_level_id = Column(Integer, primary_key=True, index=True)
    level_name = Column(String(50), unique=True, nullable=False)


class Position(Base):
    __tablename__ = "positions"
    position_id = Column(Integer, primary_key=True, index=True)
    position_name_th = Column(String(255), nullable=False)
    position_name_en = Column(String(255), nullable=False)
    position_level_id = Column(
        Integer, ForeignKey("position_levels.position_level_id"), nullable=False
    )

    # 👇 relationship to PositionLevel
    level = relationship("PositionLevel", backref="positions")


class Site(Base):
    __tablename__ = "sites"
    site_id = Column(Integer, primary_key=True, index=True)
    site_code = Column(String(50), unique=True, nullable=True)
    site_name_th = Column(String(255), nullable=False)
    site_name_en = Column(String(255), nullable=False)


class Department(Base):
    __tablename__ = "departments"
    department_id = Column(Integer, primary_key=True, index=True)
    department_name_th = Column(String(255), nullable=False)
    department_name_en = Column(String(255), nullable=False)


class Client(Base):
    __tablename__ = "clients"
    client_id = Column(Integer, primary_key=True, index=True)
    client_name = Column(String(255), nullable=False)
    contact_info = Column(String(255))


class Location(Base):
    __tablename__ = "locations"
    location_id = Column(Integer, primary_key=True, index=True)
    location_name = Column(String(255), nullable=False)
    site_id = Column(Integer, ForeignKey("sites.site_id"), nullable=False)


class DriverRole(Base):
    __tablename__ = "driver_roles"
    driver_role_id = Column(Integer, primary_key=True, index=True)
    role_name = Column(String(100), nullable=False)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    employee_id = Column(String(50), nullable=True)
    department_id = Column(Integer, ForeignKey("departments.department_id"))
    site_id = Column(Integer, ForeignKey("sites.site_id"))
    position_id = Column(Integer, ForeignKey("positions.position_id"))

    department = relationship("Department", backref="users")
    site = relationship("Site", backref="users")
    position = relationship("Position", backref="users")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    firstname = Column(String(100), nullable=True)
    lastname = Column(String(100), nullable=True)

    def set_password(self, password: str):
        self.password_hash = pwd_context.hash(password)

    def check_password(self, password: str) -> bool:
        return pwd_context.verify(password, self.password_hash)


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


# ======================================================
# 📄 CaseReport + CaseProduct
# ======================================================
class CaseReport(Base):
    __tablename__ = "case_reports"

    case_id = Column(Integer, primary_key=True, autoincrement=True)
    document_no = Column(String(50), nullable=False, unique=True)

    site_id = Column(Integer, ForeignKey("sites.site_id"), nullable=False)
    department_id = Column(Integer, ForeignKey("departments.department_id"), nullable=False)
    client_id = Column(Integer, ForeignKey("clients.client_id"))

    vehicle_id_head = Column(Integer, ForeignKey("vehicles.vehicle_id"), nullable=True)
    vehicle_id_tail = Column(Integer, ForeignKey("vehicles.vehicle_id"), nullable=True)

    origin_id = Column(Integer, ForeignKey("locations.location_id"))
    driver_role_id = Column(Integer, ForeignKey("driver_roles.driver_role_id"))
    driver_id = Column(Integer, ForeignKey("masterdrivers.driver_id"))
    incident_cause_id = Column(Integer, ForeignKey("mastercauses.cause_id"))
    reporter_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    record_date = Column(DateTime)
    incident_date = Column(DateTime)
    case_location = Column(String(255))
    destination = Column(String(255))
    case_details = Column(Text)

    estimated_cost = Column(Numeric(12, 2))
    actual_price = Column(Numeric(12, 2))

    attachments = Column(String(255))
    casestatus = Column(String(50), default="OPEN")
    vehicle_truckno = Column(String(50), nullable=True)
    priority = Column(String(20), nullable=True)

    # ✅ Relationships
    site = relationship("Site", backref="case_reports")
    department = relationship("Department", backref="case_reports")
    client = relationship("Client", backref="case_reports")

    vehicle_head = relationship("Vehicle", foreign_keys=[vehicle_id_head])
    vehicle_tail = relationship("Vehicle", foreign_keys=[vehicle_id_tail])

    driver_role = relationship("DriverRole", backref="case_reports")
    driver = relationship("MasterDriver", backref="case_reports")
    cause = relationship("MasterCause", backref="case_reports")
    reporter = relationship("User", backref="case_reports")
    origin = relationship("Location", backref="case_reports_origin")

    # 🔗 Investigation (1:1)
    investigation = relationship(
        "CaseReportInvestigate",
        back_populates="case_report",
        uselist=False,
    )

    # 🔗 Products
    products = relationship(
        "CaseProduct",
        backref="case_report",
        lazy="joined",
        cascade="all, delete-orphan",
    )
    docs = relationship(
        "CaseReportDoc",
        back_populates="case_report",
        cascade="all, delete-orphan"
    )
    def to_dict(self):
        return {
            "case_id": self.case_id,
            "document_no": self.document_no,
            "site": self.site.site_name_th if self.site else None,
            "department": self.department.department_name_th if self.department else None,
            "client": self.client.client_name if self.client else None,
            "vehicle_head": (
                f"{self.vehicle_head.vehicle_number_plate}" if self.vehicle_head else None
            ),
            "vehicle_tail": (
                f"{self.vehicle_tail.vehicle_number_plate}" if self.vehicle_tail else None
            ),
            "vehicle_truckno": self.vehicle_truckno,
            "driver": (
                f"{self.driver.first_name} {self.driver.last_name}" if self.driver else None
            ),
            "driver_role": self.driver_role.role_name if self.driver_role else None,
            "incident_cause": self.cause.cause_name if self.cause else None,
            "reporter": (
                f"{self.reporter.firstname} {self.reporter.lastname}"
                if self.reporter
                else None
            ),
            "record_date": self.record_date.isoformat() if self.record_date else None,
            "incident_date": self.incident_date.isoformat() if self.incident_date else None,
            "origin_name": self.origin.location_name if self.origin else None,
            "case_location": self.case_location,
            "destination": self.destination,
            "case_details": self.case_details,
            "products": [
                {"product_name": p.product_name, "amount": p.amount, "unit": p.unit}
                for p in self.products
            ],
                    "docs": [d.data for d in self.docs] if self.docs else [],   # 👈 FIX ADDED

            "estimated_cost": str(self.estimated_cost) if self.estimated_cost else None,
            "actual_price": str(self.actual_price) if self.actual_price else None,
            "attachments": self.attachments,
            "casestatus": self.casestatus,
            "priority": self.priority,
        }


class CaseProduct(Base):
    __tablename__ = "case_products"
    case_product_id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(Integer, ForeignKey("case_reports.case_id"))
    product_name = Column(String(100))
    amount = Column(Integer)
    unit = Column(String(50))

    @validates("amount")
    def validate_amount(self, key, value):
        if value is not None and value < 0:
            raise ValueError("amount must be >= 0")
        return value


# ======================================================
# 👤 RegisterRequest (Pydantic)
# ======================================================
class RegisterRequest(BaseModel):
    username: str
    password: str
    firstname: str
    lastname: str
    employee_id: Optional[str]
    department_id: Optional[int]
    site_id: Optional[int]
    position_id: Optional[int]


# ======================================================
# 🚨 AccidentCase + Province/District/SubDistrict
# ======================================================
class Province(Base):
    __tablename__ = "provinces"

    province_id = Column(Integer, primary_key=True, index=True)
    province_name_th = Column(String(255), nullable=False)
    province_name_en = Column(String(255), nullable=True)

    districts = relationship("District", back_populates="province")


class District(Base):
    __tablename__ = "districts"

    district_id = Column(Integer, primary_key=True, index=True)
    province_id = Column(Integer, ForeignKey("provinces.province_id"), nullable=False)
    district_code = Column(String(10), nullable=True)
    district_name_th = Column(String(100), nullable=False)
    district_name_en = Column(String(100), nullable=True)

    province = relationship("Province", back_populates="districts")
    sub_districts = relationship("SubDistrict", back_populates="district")


class SubDistrict(Base):
    __tablename__ = "sub_districts"

    sub_district_id = Column(Integer, primary_key=True, index=True)
    district_id = Column(Integer, ForeignKey("districts.district_id"), nullable=False)
    sub_district_name_th = Column(String(100), nullable=False)
    sub_district_name_en = Column(String(100), nullable=True)

    district = relationship("District", back_populates="sub_districts")


class AccidentCase(Base):
    __tablename__ = "accident_cases"

    accident_case_id = Column(Integer, primary_key=True, index=True)
    document_no_ac = Column(String(50), nullable=False, unique=True, index=True)

    site_id = Column(Integer, ForeignKey("sites.site_id"), nullable=True)
    department_id = Column(Integer, ForeignKey("departments.department_id"), nullable=True)
    client_id = Column(Integer, ForeignKey("clients.client_id"), nullable=True)
    origin_id = Column(Integer, ForeignKey("locations.location_id"), nullable=True)
    reporter_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    driver_id = Column(String(255), ForeignKey("masterdrivers.driver_id"), nullable=True)
    driver_role_id = Column(Integer, ForeignKey("driver_roles.driver_role_id"), nullable=True)
    vehicle_id_head = Column(Integer, ForeignKey("vehicles.vehicle_id"), nullable=True)
    vehicle_id_tail = Column(Integer, ForeignKey("vehicles.vehicle_id"), nullable=True)
    province_id = Column(Integer, ForeignKey("provinces.province_id"), nullable=True)
    district_id = Column(Integer, ForeignKey("districts.district_id"), nullable=True)
    sub_district_id = Column(Integer, ForeignKey("sub_districts.sub_district_id"), nullable=True)

    record_datetime = Column(DateTime, default=datetime.utcnow, nullable=False)
    incident_datetime = Column(DateTime, nullable=False)

    destination = Column(String(255), nullable=True)
    case_location = Column(String(255), nullable=True)
    police_station_area = Column(String(255), nullable=True)
    vehicle_truckno = Column(String(50), nullable=True)
    case_details = Column(String(500), nullable=True)

    alcohol_test = Column(String(50), nullable=True)
    drug_test = Column(String(50), nullable=True)
    truck_damage = Column(String(255), nullable=True)
    truck_damage_details = Column(String(255), nullable=True)

    product_damage = Column(String(255), nullable=True)
    product_damage_details = Column(String(500), nullable=True)

    injured_not_hospitalized = Column(Integer, default=0)
    injured_hospitalized = Column(Integer, default=0)
    fatalities = Column(Integer, default=0)
    injury_description = Column(String(500), nullable=True)

    other_party_full_name = Column(String(255), nullable=True)
    other_party_vehicle_plate = Column(String(50), nullable=True)
    other_party_company_name = Column(String(255), nullable=True)
    other_party_phone = Column(String(50), nullable=True)
    other_party_insurance_name = Column(String(255), nullable=True)
    other_party_claim_no = Column(String(100), nullable=True)
    claim_officer_full_name = Column(String(255), nullable=True)
    claim_officer_phone = Column(String(50), nullable=True)

    estimated_goods_damage_value = Column(Float, nullable=True)
    estimated_vehicle_damage_value = Column(Float, nullable=True)
    actual_goods_damage_value = Column(Float, nullable=True)
    actual_vehicle_damage_value = Column(Float, nullable=True)

    alcohol_test_result = Column(Float, nullable=True)
    drug_test_result = Column(String(500), nullable=True)
    attachments = Column(String(500), nullable=True)
    casestatus = Column(String(500), nullable=True)
    priority = Column(String(500), nullable=True)

    # 🔗 Relationships
    site = relationship("Site", backref="accident_cases")
    department = relationship("Department", backref="accident_cases")
    client = relationship("Client", backref="accident_cases")
    reporter = relationship("User", backref="reported_accidents")
    driver = relationship("MasterDriver", backref="accident_cases")
    driver_role = relationship("DriverRole", backref="accident_cases")
    vehicle_head = relationship("Vehicle", foreign_keys=[vehicle_id_head])
    vehicle_tail = relationship("Vehicle", foreign_keys=[vehicle_id_tail])
    origin = relationship("Location", backref="accident_cases_origin")
    province = relationship("Province", backref="accident_cases")
    district = relationship("District", backref="accident_cases")
    sub_district = relationship("SubDistrict", backref="accident_cases")

    def to_dict(self):
        return {
            "accident_case_id": self.accident_case_id,
            "document_no_ac": self.document_no_ac,
            "site_name": self.site.site_name_th if self.site else None,
            "department_name": self.department.department_name_th if self.department else None,
            "client_name": self.client.client_name if self.client else None,
            "origin_name": self.origin.location_name if self.origin else None,
            "reporter_name": (
                f"{self.reporter.firstname} {self.reporter.lastname}"
                if self.reporter
                else None
            ),
            "driver_name": (
                f"{self.driver.first_name} {self.driver.last_name}" if self.driver else None
            ),
            "driver_role_name": self.driver_role.role_name if self.driver_role else None,
            "vehicle_head_plate": (
                self.vehicle_head.vehicle_number_plate if self.vehicle_head else None
            ),
            "vehicle_tail_plate": (
                self.vehicle_tail.vehicle_number_plate if self.vehicle_tail else None
            ),
            "record_datetime": (
                self.record_datetime.isoformat() if self.record_datetime else None
            ),
            "incident_datetime": (
                self.incident_datetime.isoformat() if self.incident_datetime else None
            ),
            "province_name": self.province.province_name_th if self.province else None,
            "district_name": self.district.district_name_th if self.district else None,
            "sub_district_name": (
                self.sub_district.sub_district_name_th if self.sub_district else None
            ),
            "case_location": self.case_location,
            "police_station_area": self.police_station_area,
            "destination": self.destination,
            "truck_damage": self.truck_damage,
            "truck_damage_details": self.truck_damage_details,
            "product_damage": self.product_damage,
            "product_damage_details": self.product_damage_details,
            "case_details": self.case_details,
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
            "estimated_goods_damage_value": self.estimated_goods_damage_value,
            "estimated_vehicle_damage_value": self.estimated_vehicle_damage_value,
            "actual_goods_damage_value": self.actual_goods_damage_value,
            "actual_vehicle_damage_value": self.actual_vehicle_damage_value,
            "alcohol_test": self.alcohol_test,
            "alcohol_test_result": self.alcohol_test_result,
            "drug_test": self.drug_test,
            "drug_test_result": self.drug_test_result,
            "attachments": self.attachments,
            "casestatus": self.casestatus,
            "priority": self.priority,
        }


# ======================================================
# 🧾 CaseReportInvestigate + CorrectiveActions
# ======================================================
class CaseReportInvestigate(Base):
    __tablename__ = "case_report_investigate"

    investigate_id = Column(Integer, primary_key=True, index=True)
    document_no = Column(
        String(50),
        ForeignKey("case_reports.document_no"),
        nullable=False,
    )

    root_cause_analysis = Column(Text)
    claim_type = Column(Text)
    insurance_claim = Column(Text)
    product_resellable = Column(Text)
    remaining_damage_cost = Column(Integer)
    driver_cost = Column(Integer)
    company_cost = Column(Integer)

    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # 🔗 Parent (CaseReport) – 1:1
    case_report = relationship(
        "CaseReport",
        back_populates="investigation",
    )

    # 🔗 Children (Corrective actions) – 1:N
    corrective_actions = relationship(
        "CaseReportCorrectiveAction",
        back_populates="investigation",
        cascade="all, delete-orphan",
    )


class CaseReportCorrectiveAction(Base):
    __tablename__ = "case_report_corrective_actions"

    id = Column(Integer, primary_key=True)
    investigate_id = Column(
        Integer,
        ForeignKey("case_report_investigate.investigate_id", ondelete="CASCADE"),
        nullable=False,
    )

    corrective_action = Column(Text)
    pic_contract = Column(Text)
    plan_date = Column(Date)
    action_completed_date = Column(Date)

    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # ✅ Many-to-one back to CaseReportInvestigate
    investigation = relationship(
        "CaseReportInvestigate",
        back_populates="corrective_actions",
    )
class CaseReportDoc(Base):
    __tablename__ = "case_report_docs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(Integer, ForeignKey("case_reports.case_id", ondelete="CASCADE"), nullable=False)

    data = Column(JSONB, nullable=False)  # <-- corrected!

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    case_report = relationship("CaseReport", back_populates="docs")