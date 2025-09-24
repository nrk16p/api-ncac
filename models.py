from datetime import datetime
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Text, Numeric
from sqlalchemy.orm import relationship, validates
from passlib.context import CryptContext
from database import Base
from pydantic import BaseModel
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class PositionLevel(Base):
    __tablename__ = "position_levels"
    position_level_id = Column(Integer, primary_key=True, index=True)
    level_name = Column(String(50), unique=True, nullable=False)

class Position(Base):
    __tablename__ = "positions"
    position_id = Column(Integer, primary_key=True, index=True)
    position_name_th = Column(String(255), nullable=False)
    position_name_en = Column(String(255), nullable=False)
    position_level_id = Column(Integer, ForeignKey("position_levels.position_level_id"), nullable=False)
    level = relationship("PositionLevel", backref="positions")   # 👈 ตรงนี้ใช้ชื่อ level

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
    location_address = Column(String(255), nullable=True)

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

    site = relationship("Site", backref="case_reports")
    department = relationship("Department", backref="case_reports")
    client = relationship("Client", backref="case_reports")
    vehicle_head = relationship("Vehicle", foreign_keys=[vehicle_id_head])
    vehicle_tail = relationship("Vehicle", foreign_keys=[vehicle_id_tail])
    vehicle_truckno = Column(String(50), nullable=True)
    driver_role = relationship("DriverRole", backref="case_reports")
    driver = relationship("MasterDriver", backref="case_reports")
    cause = relationship("MasterCause", backref="case_reports")
    reporter = relationship("User", backref="case_reports")

    products = relationship("CaseProduct", backref="case_report", lazy="joined", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "case_id": self.case_id,
            "document_no": self.document_no,
            "site": self.site.site_name_th if self.site else None,
            "department": self.department.department_name_th if self.department else None,
            "client": self.client.client_name if self.client else None,
            "vehicle_head": f"{self.vehicle_head.vehicle_number_plate}" if self.vehicle_head else None,
            "vehicle_tail": f"{self.vehicle_tail.vehicle_number_plate}" if self.vehicle_tail else None,
            "vehicle_truckno": self.vehicle_truckno,
            "driver": f"{self.driver.first_name} {self.driver.last_name}" if self.driver else None,
            "driver_role": self.driver_role.role_name if self.driver_role else None,
            "incident_cause": self.cause.cause_name if self.cause else None,
            "reporter": f"{self.reporter.firstname} {self.reporter.lastname}" if self.reporter else None,
            "record_date": self.record_date.isoformat() if self.record_date else None,
            "incident_date": self.incident_date.isoformat() if self.incident_date else None,
            "case_location": self.case_location,
            "destination": self.destination,
            "case_details": self.case_details,
            "products": [
                {"product_name": p.product_name, "amount": p.amount, "unit": p.unit}
                for p in self.products
            ],
            "estimated_cost": str(self.estimated_cost) if self.estimated_cost else None,
            "actual_price": str(self.actual_price) if self.actual_price else None,
            "attachments": self.attachments,
            "casestatus": self.casestatus,
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
from pydantic import BaseModel
from typing import Optional


class RegisterRequest(BaseModel):
    username: str
    password: str
    firstname: str
    lastname: str
    employee_id: Optional[str]
    department_id: Optional[int]
    site_id: Optional[int]
    position_id: Optional[int]
    
