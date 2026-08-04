"""
AC Investigation Report (Part 2) — Root Cause Analysis, Measures & Risk Assessment

โครงสร้าง 1 เอกสาร (document_no_ac) : 1 การสอบสวน
    accident_case_investigate                 (parent)
    ├── accident_case_investigate_whys        (Why-Why Analysis 5 ข้อ ขึ้นไป)
    ├── accident_case_investigate_root_causes (สาเหตุที่แท้จริง 5M1E)
    ├── accident_case_investigate_measures    (มาตรการ ผูกกับ root cause ผ่าน client uid)
    └── accident_case_investigate_investigators (ผู้เข้าร่วมสอบสวน)

หมายเหตุสำคัญ:
ทุกตารางลูกเก็บ `client_id` = uid ที่ฝั่ง FE สร้างขึ้น (ACInvestigate.tsx → uid())
ต้อง round-trip ค่านี้กลับไปให้ FE เสมอ เพราะ
  1. measures อ้างอิง root cause ด้วย uid (root_cause_id)
  2. ไฟล์แนบของมาตรการถูกตั้งชื่อเป็น `{document_no}_measure_{uid}_{rand}.ext`
     ถ้า uid เปลี่ยน ไฟล์แนบเดิมจะหาไม่เจอ
"""

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Date,
    TIMESTAMP,
    ForeignKey,
    Index,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from database import Base


# ============================================================
# PARENT
# ============================================================
class AccidentCaseInvestigate(Base):
    __tablename__ = "accident_case_investigate"

    investigate_ac_id = Column(Integer, primary_key=True, autoincrement=True)
    document_no_ac = Column(
        String(50),
        ForeignKey("accident_cases.document_no_ac", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # --- Type of Accident (multi-select) ---
    accident_types = Column(JSONB, default=list)

    # --- Severity / Description ---
    severity_level = Column(String(10))          # L1 - L5
    accident_description = Column(Text)

    # --- Risk Assessment ---
    risk_assessment_completed = Column(String(10))   # yes | no
    risk_assessment_reviewed = Column(String(10))    # yes | no | na
    risk_assessment_result = Column(String(30))      # required_revise | not_required_revise
    risk_assessment_date = Column(Date)
    risk_assessment_team = Column(Text)
    risk_assessment_attached = Column(String(10))    # yes | no | na

    # --- Conclusion ---
    avoidability = Column(String(20))                # avoidable | unavoidable

    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = Column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # --- Relationships ---
    accident_case = relationship("AccidentCase", back_populates="investigation")

    why_analysis = relationship(
        "AccidentCaseInvestigateWhy",
        back_populates="investigate",
        cascade="all, delete-orphan",
        order_by="AccidentCaseInvestigateWhy.seq",
    )
    root_causes = relationship(
        "AccidentCaseInvestigateRootCause",
        back_populates="investigate",
        cascade="all, delete-orphan",
        order_by="AccidentCaseInvestigateRootCause.seq",
    )
    measures = relationship(
        "AccidentCaseInvestigateMeasure",
        back_populates="investigate",
        cascade="all, delete-orphan",
        order_by="AccidentCaseInvestigateMeasure.seq",
    )
    investigators = relationship(
        "AccidentCaseInvestigateInvestigator",
        back_populates="investigate",
        cascade="all, delete-orphan",
        order_by="AccidentCaseInvestigateInvestigator.seq",
    )

    # FE (investigate_AC) รู้จักฟิลด์นี้ในชื่อ investigate_id
    @property
    def investigate_id(self) -> int:
        return self.investigate_ac_id

    @property
    def is_complete(self) -> bool:
        """
        การสอบสวนถือว่าครบเมื่อ:
        - เลือกชนิดอุบัติเหตุ / ระดับความรุนแรง / รายละเอียด / ข้อสรุปหลีกเลี่ยงได้หรือไม่
        - มีสาเหตุที่แท้จริงอย่างน้อย 1 ข้อ พร้อมประเภท 5M1E
        - ทุกสาเหตุมีมาตรการอย่างน้อย 1 ข้อ และทุกมาตรการมีผู้รับผิดชอบ + แผนดำเนินการ
        - มีผู้เข้าร่วมสอบสวนอย่างน้อย 1 คน
        (action_completed_date ปล่อยว่างได้ เพราะยังดำเนินการไม่เสร็จ)
        """
        def filled(value) -> bool:
            return value is not None and str(value).strip() != ""

        if not self.accident_types:
            return False
        if not all(
            filled(v)
            for v in (self.severity_level, self.accident_description, self.avoidability)
        ):
            return False

        if not self.root_causes:
            return False
        if not any(filled(p.name) for p in self.investigators):
            return False

        measures_by_rc = {}
        for m in self.measures:
            measures_by_rc.setdefault(m.root_cause_id, []).append(m)

        for rc in self.root_causes:
            if not filled(rc.root_cause) or not filled(rc.category):
                return False
            rc_measures = measures_by_rc.get(rc.id, [])
            if not rc_measures:
                return False
            for m in rc_measures:
                if not filled(m.measure) or not filled(m.pic_contract) or not m.plan_date:
                    return False

        return True

    def to_dict(self):
        return {
            "investigate_id": self.investigate_ac_id,
            "document_no_ac": self.document_no_ac,
            "is_complete": self.is_complete,
            "accident_types": self.accident_types or [],
            "severity_level": self.severity_level,
            "accident_description": self.accident_description,
            "risk_assessment_completed": self.risk_assessment_completed,
            "risk_assessment_reviewed": self.risk_assessment_reviewed,
            "risk_assessment_result": self.risk_assessment_result,
            "risk_assessment_date": self.risk_assessment_date.isoformat()
            if self.risk_assessment_date
            else None,
            "risk_assessment_team": self.risk_assessment_team,
            "risk_assessment_attached": self.risk_assessment_attached,
            "avoidability": self.avoidability,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "why_analysis": [w.to_dict() for w in self.why_analysis],
            "root_causes": [rc.to_dict() for rc in self.root_causes],
            "measures": [m.to_dict() for m in self.measures],
            "investigators": [p.to_dict() for p in self.investigators],
        }


# ============================================================
# CHILD : WHY-WHY ANALYSIS
# ============================================================
class AccidentCaseInvestigateWhy(Base):
    __tablename__ = "accident_case_investigate_whys"

    why_id = Column(Integer, primary_key=True, autoincrement=True)
    investigate_ac_id = Column(
        Integer,
        ForeignKey("accident_case_investigate.investigate_ac_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # uid ที่ FE สร้าง — เปิดเผยออก API ในชื่อ "id"
    id = Column("client_id", String(64), nullable=False)
    seq = Column(Integer, nullable=False, default=1)
    problem = Column(Text)
    cause = Column(Text)

    investigate = relationship("AccidentCaseInvestigate", back_populates="why_analysis")

    def to_dict(self):
        return {
            "id": self.id,
            "seq": self.seq,
            "problem": self.problem,
            "cause": self.cause,
        }


# ============================================================
# CHILD : ROOT CAUSE (5M1E)
# ============================================================
class AccidentCaseInvestigateRootCause(Base):
    __tablename__ = "accident_case_investigate_root_causes"

    root_cause_pk = Column(Integer, primary_key=True, autoincrement=True)
    investigate_ac_id = Column(
        Integer,
        ForeignKey("accident_case_investigate.investigate_ac_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    id = Column("client_id", String(64), nullable=False)
    seq = Column(Integer, nullable=False, default=1)
    root_cause = Column(Text)
    # man | machine | material | method | measurement | environment
    category = Column(String(30))

    investigate = relationship("AccidentCaseInvestigate", back_populates="root_causes")

    def to_dict(self):
        return {
            "id": self.id,
            "seq": self.seq,
            "root_cause": self.root_cause,
            "category": self.category,
        }


# ============================================================
# CHILD : PREVENTIVE / CORRECTIVE MEASURE
# ============================================================
class AccidentCaseInvestigateMeasure(Base):
    __tablename__ = "accident_case_investigate_measures"

    measure_pk = Column(Integer, primary_key=True, autoincrement=True)
    investigate_ac_id = Column(
        Integer,
        ForeignKey("accident_case_investigate.investigate_ac_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    id = Column("client_id", String(64), nullable=False)
    # uid ของ root cause ที่มาตรการนี้ผูกอยู่ (ไม่ใช่ PK ฝั่ง DB)
    root_cause_id = Column("root_cause_client_id", String(64), nullable=False)
    seq = Column(Integer, nullable=False, default=1)
    measure = Column(Text)
    pic_contract = Column(Text)
    plan_date = Column(Date)
    action_completed_date = Column(Date)

    investigate = relationship("AccidentCaseInvestigate", back_populates="measures")

    def to_dict(self):
        return {
            "id": self.id,
            "seq": self.seq,
            "root_cause_id": self.root_cause_id,
            "measure": self.measure,
            "pic_contract": self.pic_contract,
            "plan_date": self.plan_date.isoformat() if self.plan_date else None,
            "action_completed_date": self.action_completed_date.isoformat()
            if self.action_completed_date
            else None,
        }


# ============================================================
# CHILD : INVESTIGATORS
# ============================================================
class AccidentCaseInvestigateInvestigator(Base):
    __tablename__ = "accident_case_investigate_investigators"

    investigator_pk = Column(Integer, primary_key=True, autoincrement=True)
    investigate_ac_id = Column(
        Integer,
        ForeignKey("accident_case_investigate.investigate_ac_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    id = Column("client_id", String(64), nullable=False)
    seq = Column(Integer, nullable=False, default=1)
    name = Column(String(255))
    position = Column(String(255))

    investigate = relationship("AccidentCaseInvestigate", back_populates="investigators")

    def to_dict(self):
        return {
            "id": self.id,
            "seq": self.seq,
            "name": self.name,
            "position": self.position,
        }


# ค้นหามาตรการตามสาเหตุได้เร็วขึ้นเวลา report
Index(
    "ix_ac_inv_measures_root_cause",
    AccidentCaseInvestigateMeasure.investigate_ac_id,
    AccidentCaseInvestigateMeasure.root_cause_id,
)
