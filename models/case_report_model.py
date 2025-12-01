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
    # ✅ Newly added columns v2
    origin_id = Column(Integer, ForeignKey("locations.location_id"), nullable=True)
    vehicle_id_head = Column(Integer, nullable=True)
    vehicle_id_tail = Column(Integer, nullable=True)
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

    # ✅ FIX: Add missing relationship to link with CaseReportInvestigate
    investigation = relationship(
        "CaseReportInvestigate",
        back_populates="case_report",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # Existing relationships
    site = relationship("Site", backref="case_reports")
    department = relationship("Department", backref="case_reports")
    reporter = relationship("User", backref="case_reports")
    driver = relationship("MasterDriver", backref="case_reports")
    driver_role = relationship("DriverRole", backref="case_reports")

    docs = relationship(
        "CaseReportDoc",
        back_populates="case_report",
        cascade="all, delete-orphan",
    )

    def to_dict(self):
        return {
            "case_id": self.case_id,
            "document_no": self.document_no,
            "site_name": self.site.site_name_th if self.site else None,
            "department_name": self.department.department_name_th if self.department else None,
            "reporter_name": f"{self.reporter.firstname} {self.reporter.lastname}" if self.reporter else None,
            "case_details": self.case_details,
            "priority": self.priority,
            "casestatus": self.casestatus,
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


class CaseReportDoc(Base):
    __tablename__ = "case_report_docs"

    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("case_reports.case_id", ondelete="CASCADE"))
    data = Column(JSONB, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    case_report = relationship("CaseReport", back_populates="docs")
