from sqlalchemy import Column, Integer, String, Date, DateTime, Float, Text, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime
import pytz
from fastapi import Header

bangkok = pytz.timezone("Asia/Bangkok")



class SafetyTalk(Base):
    __tablename__ = "safety_talk"

    safety_talk_id = Column(Integer, primary_key=True, index=True)

    inspection_task_id = Column(
        String,
        ForeignKey("inspection_task.inspection_task_id"),
        unique=True
    )

    # 🔥 NEW
    topics = Column(JSONB)       # list of topics
    attendance = Column(JSONB)   # list of drivers attendance

    # existing
    noted = Column(Text)
    upload_url = Column(Text)


class DrugTest(Base):
    __tablename__ = "drug_test"

    drug_test_id = Column(Integer, primary_key=True, index=True)

    # ✅ NEW: status
    drug_test_status = Column(String, default="pending")  
    # เช่น pending / pass / fail

    # Alcohol
    alcohol = Column(Float)
    alcohol_attachment = Column(Text)

    # Amphetamine
    amfetamin = Column(String)
    amfetamin_attachment = Column(String)

    # KRA
    kra = Column(String)
    kra_attachment = Column(String)

    # THC
    thc = Column(String)
    thc_attachment = Column(String)


class PPETest(Base):
    __tablename__ = "ppe_test"

    ppe_test_id = Column(Integer, primary_key=True, index=True)

    # เดิม
    shirt_check = Column(String, nullable=True)
    vest_size = Column(String, nullable=True)

    boot_check = Column(String, nullable=True)
    safety_shoes_size = Column(String, nullable=True)

    # ✅ เพิ่ม PPE ใหม่
    helmet_check = Column(String, nullable=True)          # หมวก Safety
    glasses_check = Column(String, nullable=True)         # แว่นตา Safety
    mask_check = Column(String, nullable=True)            # ผ้าปิดจมูก
    vest_check = Column(String, nullable=True)            # เสื้อสะท้อนแสง
    glove_check = Column(String, nullable=True)           # ถุงมือ Safety
    safety_shoes_check = Column(String, nullable=True)    # รองเท้า Safety
    ppe_status = Column(String, nullable=True, default="pending")
    # attachment
    ppe_attachment = Column(String, nullable=True)

class VehicleInspect(Base):
    __tablename__ = "vehicle_inspect"

    vehicle_inspect_id = Column(Integer, primary_key=True, index=True)

    # ✅ checklist ทั้งหมด (เก็บเป็น JSON)
    checklist = Column(JSONB)

    # ✅ รูปรอบรถ (หน้า/ซ้าย/ขวา/หลัง)
    around_check_attachment = Column(ARRAY(String))

    # ✅ รูปในห้องโดยสาร
    cockpit_attachment = Column(String)

    vechicle_status = Column(String, nullable=True, default="pending")



class InspectionTask(Base):
    __tablename__ = "inspection_task"

    inspection_task_id = Column(String, primary_key=True, index=True)

    trainer_id = Column(String)    
    client_name = Column(String)
    plant_code = Column(String)
    plant_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    plan_date = Column(Date)
    action_date = Column(DateTime)
    drug_test_attachment = Column(String)
    inspection_task_status = Column(String)

    drivers = relationship("InspectionTaskDriver", back_populates="task")

class InspectionTaskDriver(Base):
    __tablename__ = "inspection_task_driver"
    inspection_date = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(bangkok)
    )

    inspection_task_driver_id = Column(String, primary_key=True, index=True)

    inspection_task_id = Column(
        String,
        ForeignKey("inspection_task.inspection_task_id")
    )

    driver_id = Column(String)      # changed to string
    number_plate = Column(String)       # changed to string
    truck_number = Column(String)   # new field
    first_name = Column(String)
    last_name = Column(String)
    status = Column(String)
    truck_type = Column(String)

    drug_test_id = Column(
        Integer,
        ForeignKey("drug_test.drug_test_id"),
        nullable=True
    )

    ppe_test_id = Column(
        Integer,
        ForeignKey("ppe_test.ppe_test_id"),
        nullable=True
    )

    vehicle_inspect_id = Column(
        Integer,
        ForeignKey("vehicle_inspect.vehicle_inspect_id"),
        nullable=True
    )

    inspection_task_driver_status = Column(String)

    task = relationship("InspectionTask", back_populates="drivers")

    drug_test = relationship("DrugTest")
    ppe_test = relationship("PPETest")
    vehicle_inspect = relationship("VehicleInspect")


class InspectionTaskLog(Base):
    __tablename__ = "inspection_task_log"

    id = Column(Integer, primary_key=True, index=True)

    inspection_task_id = Column(String, index=True)
    action = Column(String)  # DELETE / UPDATE / CREATE

    action_by = Column(String)  # employee_id หรือ username
    remark = Column(Text)

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(bangkok)
    )