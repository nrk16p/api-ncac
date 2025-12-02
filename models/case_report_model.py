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

    # ✅ RelationshipsUPDATE
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
    origin = relationship("Location", backref="case_report")

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
    
            # 🔹 Linked Names
            "site_name": getattr(self.site, "site_name_th", None),
            "department_name": getattr(self.department, "department_name_th", None),
            "client_name": getattr(self.client, "client_name", None),
            "origin_name": getattr(self.origin, "location_name", None),
            "incident_cause": getattr(self.incident_cause, "cause_name", None),
    
            # 🔹 Foreign Key IDs (for edit forms)
            "site_id": self.site_id,
            "department_id": self.department_id,
            "client_id": self.client_id,
            "origin_id": self.origin_id,
            "incident_cause_id": self.incident_cause_id,
    
            # 🔹 Vehicle details
            "vehicle_head_plate": getattr(self.vehicle_head, "vehicle_number_plate", None),
            "vehicle_tail_plate": getattr(self.vehicle_tail, "vehicle_number_plate", None),
            "vehicle_truckno": self.vehicle_truckno,
    
            # 🔹 Driver info
            "driver_name": (
                f"{getattr(self.driver, 'first_name', '')} {getattr(self.driver, 'last_name', '')}".strip()
                if self.driver else None
            ),
            "driver_role_name": getattr(self.driver_role, "role_name", None),
    
            # 🔹 Reporter info
            "reporter_name": (
                f"{getattr(self.reporter, 'firstname', '')} {getattr(self.reporter, 'lastname', '')}".strip()
                if self.reporter else None
            ),
    
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
    
            # 🔹 Cost info (keep as float)
            "estimated_cost": float(self.estimated_cost) if self.estimated_cost is not None else None,
            "actual_price": float(self.actual_price) if self.actual_price is not None else None,
    
            # 🔹 Investigation (if any)
            "investigation": self.investigation.to_dict() if getattr(self, "investigation", None) else None,
    
            # 🔹 Misc
            "attachments": self.attachments,
            "casestatus": self.casestatus,
            "priority": self.priority,
    
            # 🔹 Documents (related)
            "docs": [d.data for d in self.docs] if getattr(self, "docs", None) else [],
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

    # ✅ Relationship back to CaseReport
    case_report = relationship("CaseReport", back_populates="investigation")

    # ✅ Relationship to corrective actions
    corrective_actions = relationship(
        "CaseReportCorrectiveAction",
        back_populates="investigate",
        cascade="all, delete-orphan",
    )

    # ✅ NEW METHOD
    def to_dict(self):
        return {
            "investigate_id": self.investigate_id,
            "document_no": self.document_no,
            "root_cause_analysis": self.root_cause_analysis,
            "claim_type": self.claim_type,
            "insurance_claim": self.insurance_claim,
            "product_resellable": self.product_resellable,
            "remaining_damage_cost": self.remaining_damage_cost,
            "driver_cost": self.driver_cost,
            "company_cost": self.company_cost,
            "event_img": self.event_img,
            "event_img_remark": self.event_img_remark,
            "account_attachment": self.account_attachment,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,

            # 🔹 Include corrective actions if any
            "corrective_actions": [
                {
                    "id": c.id,
                    "corrective_action": c.corrective_action,
                    "plan_date": c.plan_date.isoformat() if c.plan_date else None,
                    "pic_contract": c.pic_contract,
                    "action_completed_date": c.action_completed_date.isoformat()
                    if c.action_completed_date else None,
                }
                for c in getattr(self, "corrective_actions", [])
            ],
        }


class CaseReportCorrectiveAction(Base):
    __tablename__ = "case_report_corrective_actions"

    id = Column(Integer, primary_key=True)
    investigate_id = Column(Integer, ForeignKey("case_report_investigate.investigate_id"))
    corrective_action = Column(Text)
    plan_date = Column(Date)
    pic_contract = Column(Text)
    action_completed_date = Column(Date)


    # ✅ Back reference
    investigate = relationship("CaseReportInvestigate", back_populates="corrective_actions")


class CaseReportDoc(Base):
    __tablename__ = "case_report_docs"

    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("case_reports.case_id", ondelete="CASCADE"))
    data = Column(JSONB, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    case_report = relationship("CaseReport", back_populates="docs")
