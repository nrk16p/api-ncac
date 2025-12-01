from datetime import datetime
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
    func,
)
from sqlalchemy.orm import relationship, validates
from sqlalchemy.dialects.postgresql import JSONB
from database import Base


class CaseReport(Base):
    __tablename__ = "case_reports"

    case_id = Column(Integer, primary_key=True, autoincrement=True)
    document_no = Column(String(50), unique=True, nullable=False)

    site_id = Column(Integer, ForeignKey("sites.site_id"))
    department_id = Column(Integer, ForeignKey("departments.department_id"))
    client_id = Column(Integer, ForeignKey("clients.client_id"))
    driver_id = Column(Integer, ForeignKey("masterdrivers.driver_id"))
    reporter_id = Column(Integer, ForeignKey("users.id"))
    driver_role_id = Column(Integer, ForeignKey("driver_roles.driver_role_id"))
    origin_id = Column(Integer, ForeignKey("locations.location_id"), nullable=True)

    # ✅ Fixed
    vehicle_id_head = Column(Integer, ForeignKey("vehicles.vehicle_id"), nullable=True)
    vehicle_id_tail = Column(Integer, ForeignKey("vehicles.vehicle_id"), nullable=True)

    vehicle_truckno = Column(String(50), nullable=True)
    incident_cause_id = Column(Integer, ForeignKey("mastercauses.cause_id"), nullable=True)

    record_date = Column(DateTime)
    incident_date = Column(DateTime)
    case_location = Column(String(255))
    destination = Column(String(255))
    case_details = Column(Text)
    estimated_cost = Column(Numeric(12, 2))
    actual_price = Column(Numeric(12, 2))
    attachments = Column(String(255))
    casestatus = Column(String(50))
    priority = Column(String(20))

    # ✅ Relationships
    site = relationship("Site", backref="case_reports")
    department = relationship("Department", backref="case_reports")
    reporter = relationship("User", backref="case_reports")
    driver = relationship("MasterDriver", backref="case_reports")
    driver_role = relationship("DriverRole", backref="case_reports")
    client = relationship("Client", backref="case_reports")
    incident_cause = relationship("MasterCause", backref="case_reports")
    vehicle_head = relationship("Vehicle", foreign_keys=[vehicle_id_head], backref="head_cases")
    vehicle_tail = relationship("Vehicle", foreign_keys=[vehicle_id_tail], backref="tail_cases")
    products = relationship("CaseProduct", backref="case_report", cascade="all, delete-orphan")

    investigation = relationship(
        "CaseReportInvestigate",
        back_populates="case_report",
        uselist=False,
        cascade="all, delete-orphan",
    )
    docs = relationship("CaseReportDoc", back_populates="case_report", cascade="all, delete-orphan")
    #update version
    def to_dict(self):
        return {
            "case_id": self.case_id,
            "document_no": self.document_no,
    
            # 🔹 Linked names
            "site": self.site.site_name_th if self.site else None,
            "department": self.department.department_name_th if self.department else None,
            "client": self.client.client_name if hasattr(self, "client") and self.client else None,
    
            # 🔹 Vehicle details
            "vehicle_head": self.vehicle_head.vehicle_number_plate if getattr(self, "vehicle_head", None) else None,
            "vehicle_tail": self.vehicle_tail.vehicle_number_plate if getattr(self, "vehicle_tail", None) else None,
            "vehicle_truckno": self.vehicle_truckno,
    
            # 🔹 Driver info
            "driver": f"{self.driver.first_name} {self.driver.last_name}" if self.driver else None,
            "driver_role": self.driver_role.role_name if self.driver_role else None,
    
            # 🔹 Cause of incident (from mastercauses)
            "incident_cause": self.incident_cause.cause_name if getattr(self, "incident_cause", None) else None,
    
            # 🔹 Reporter
            "reporter": f"{self.reporter.firstname} {self.reporter.lastname}" if self.reporter else None,
    
            # 🔹 Dates & details
            "record_date": self.record_date.isoformat() if self.record_date else None,
            "incident_date": self.incident_date.isoformat() if self.incident_date else None,
            "case_location": self.case_location,
            "destination": self.destination,
            "case_details": self.case_details,
    
            # 🔹 Products
            "products": [
                {
                    "product_name": p.product_name,
                    "amount": float(p.amount) if p.amount is not None else None,
                    "unit": p.unit
                }
                for p in getattr(self, "products", [])
            ],
    
            # 🔹 Cost info
            "estimated_cost": f"{self.estimated_cost:.2f}" if self.estimated_cost is not None else None,
            "actual_price": f"{self.actual_price:.2f}" if self.actual_price is not None else None,
    
            # 🔹 Misc
            "attachments": self.attachments,
            "casestatus": self.casestatus,
            "priority": self.priority,
            "docs": [d.data for d in self.docs],
        }


class CaseProduct(Base):
    __tablename__ = "case_products"

    case_product_id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("case_reports.case_id"))
    product_name = Column(String(100))
    amount = Column(Integer)
    unit = Column(String(50))

    @validates("amount")
    def validate_amount(self, key, value):
        if value < 0:
            raise ValueError("Amount must be >= 0")
        return value


class CaseReportInvestigate(Base):
    __tablename__ = "case_report_investigate"

    investigate_id = Column(Integer, primary_key=True)
    document_no = Column(String(50), ForeignKey("case_reports.document_no"))
    root_cause_analysis = Column(Text)
    claim_type = Column(Text)
    insurance_claim = Column(Text)
    product_resellable = Column(Text)
    remaining_damage_cost = Column(Integer)
    driver_cost = Column(Integer)
    company_cost = Column(Integer)
    event_img = Column(Text)
    event_img_remark = Column(Text)
    account_attachment = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), onupdate=func.now())

    # ✅ Match back_populates with CaseReport
    case_report = relationship("CaseReport", back_populates="investigation")


class CaseReportCorrectiveAction(Base):
    __tablename__ = "case_report_corrective_actions"

    id = Column(Integer, primary_key=True)
    investigate_id = Column(Integer, ForeignKey("case_report_investigate.investigate_id"))
    corrective_action = Column(Text)
    plan_date = Column(Date)
    pic_contract = Column(Text)
    action_completed_date = Column(Date)


class CaseReportDoc(Base):
    __tablename__ = "case_report_docs"

    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("case_reports.case_id", ondelete="CASCADE"))
    data = Column(JSONB, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    case_report = relationship("CaseReport", back_populates="docs")
