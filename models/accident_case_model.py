from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Float, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from database import Base


class AccidentCase(Base):
    __tablename__ = "accident_cases"

    accident_case_id = Column(Integer, primary_key=True)
    document_no_ac = Column(String(50), unique=True, nullable=False)
    site_id = Column(Integer, ForeignKey("sites.site_id"))
    department_id = Column(Integer, ForeignKey("departments.department_id"))
    reporter_id = Column(Integer, ForeignKey("users.id"))
    driver_id = Column(Integer, ForeignKey("masterdrivers.driver_id"))
    driver_role_id = Column(Integer, ForeignKey("driver_roles.driver_role_id"))
    vehicle_id_head = Column(Integer, ForeignKey("vehicles.vehicle_id"))
    vehicle_id_tail = Column(Integer, ForeignKey("vehicles.vehicle_id"))
    province_id = Column(Integer, ForeignKey("provinces.province_id"))

    record_datetime = Column(DateTime, default=datetime.utcnow)
    incident_datetime = Column(DateTime)
    case_location = Column(String(255))
    destination = Column(String(255))
    police_station_area = Column(String(255))
    priority = Column(String(100))
    casestatus = Column(String(100))
    attachments = Column(String(500))

    site = relationship("Site")
    department = relationship("Department")
    reporter = relationship("User")
    driver = relationship("MasterDriver")
    driver_role = relationship("DriverRole")
    vehicle_head = relationship("Vehicle", foreign_keys=[vehicle_id_head])
    vehicle_tail = relationship("Vehicle", foreign_keys=[vehicle_id_tail])
    province = relationship("Province")
    docs = relationship("AccidentCaseDoc", back_populates="accident_case", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "accident_case_id": self.accident_case_id,
            "document_no_ac": self.document_no_ac,
            "site_name": self.site.site_name_th if self.site else None,
            "department_name": self.department.department_name_th if self.department else None,
            "driver_name": f"{self.driver.first_name} {self.driver.last_name}" if self.driver else None,
            "priority": self.priority,
            "casestatus": self.casestatus,
            "attachments": self.attachments,
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
