from sqlalchemy import Column, Integer, String, ForeignKey,Text, Boolean,TIMESTAMP, Numeric, UniqueConstraint
from sqlalchemy.orm import relationship
from database import Base
    

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
    site_id = Column(Integer)



class Vehicle(Base):
    __tablename__ = "vehicles"
    vehicle_id = Column(Integer, primary_key=True, index=True)
    truck_no = Column(String(50), nullable=False)
    vehicle_number_plate = Column(String(50), nullable=False)
    plate_type = Column(String(50), nullable=False)


from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey, TIMESTAMP, Numeric
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base

# =========================
# FORM MASTER (TEMPLATE)
# =========================

class FormMaster(Base):
    __tablename__ = "form_masters"

    id = Column(Integer, primary_key=True, index=True)
    form_type = Column(String(20), nullable=False)
    form_code = Column(String(50), unique=True, nullable=False)
    form_name = Column(String(255))
    form_status = Column(String(20), default="Draft")

    need_approval = Column(Boolean, default=True)   # ✅ MUST EXIST

    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    questions = relationship("FormQuestion", back_populates="form", cascade="all, delete")
    submissions = relationship("FormSubmission", back_populates="form")

# =========================
# FORM QUESTIONS
# =========================
class FormQuestion(Base):
    __tablename__ = "form_questions"

    id = Column(Integer, primary_key=True, index=True)
    form_master_id = Column(Integer, ForeignKey("form_masters.id", ondelete="CASCADE"))
    question_name = Column(String(100), nullable=False)
    question_label = Column(String(255), nullable=False)
    question_type = Column(String(30), nullable=False)

    is_required = Column(Boolean, default=False)
    sort_order = Column(Integer, default=0)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    form = relationship("FormMaster", back_populates="questions")
    options = relationship("FormQuestionOption", back_populates="question", cascade="all, delete")


# =========================
# QUESTION OPTIONS
# =========================
class FormQuestionOption(Base):
    __tablename__ = "form_question_options"

    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(Integer, ForeignKey("form_questions.id", ondelete="CASCADE"))
    option_value = Column(String(100), nullable=False)
    option_label = Column(String(255), nullable=False)
    option_filter = Column(String(100), nullable=True)   # 👈 ADD THIS

    sort_order = Column(Integer, default=0)

    question = relationship("FormQuestion", back_populates="options")


# =========================
# FORM SUBMISSION (INSTANCE)
# =========================
class FormSubmission(Base):
    __tablename__ = "form_submissions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)  # ✅ MUST AUTOINCREMENT

    form_master_id = Column(Integer, ForeignKey("form_masters.id"))

    form_id = Column(String(100), unique=True, index=True)   # ✅ BUSINESS ID

    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

    created_by = Column(String(50), nullable=True)   # employee_id
    updated_by = Column(String(50), nullable=True)   # employee_id

    status_approve = Column(String(20), default="In Progress")

    form = relationship("FormMaster", back_populates="submissions")
    values = relationship("FormSubmissionValue", back_populates="submission", cascade="all, delete")



# =========================
# SUBMISSION VALUES
# =========================
class FormSubmissionValue(Base):
    __tablename__ = "form_submission_values"

    id = Column(Integer, primary_key=True, index=True)
    submission_id = Column(Integer, ForeignKey("form_submissions.id"), nullable=False)  # ✅ NOT NULL
    question_id = Column(Integer, ForeignKey("form_questions.id"))

    value_text = Column(Text)
    value_number = Column(Numeric)
    value_date = Column(TIMESTAMP)
    value_boolean = Column(Boolean)

    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    submission = relationship("FormSubmission", back_populates="values")
    question = relationship("FormQuestion")   # ✅ REQUIRED


class FormSequence(Base):
    __tablename__ = "form_sequences"

    id = Column(Integer, primary_key=True, index=True)
    form_code = Column(String(50), nullable=False)
    year = Column(Integer, nullable=False)
    last_number = Column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("form_code", "year", name="uq_form_code_year"),
    )

