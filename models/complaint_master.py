from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, UniqueConstraint
)
from sqlalchemy.orm import relationship
from datetime import datetime

from database import Base


# =========================================================
# COMPLAINT MASTER — "ประเภทเรื่อง" ของแต่ละหน่วยงาน
#
# เดิมรายการนี้ hard-code อยู่ฝั่งหน้าจอ (hr-service/lib/data.ts) แก้ทีต้อง
# deploy ใหม่ทุกครั้ง ย้ายมาเป็นตารางเพื่อให้แก้ผ่าน API ได้
#
# driver_complaints.problem ชี้มาที่ตารางนี้ (คอลัมน์นั้นเก็บ "ประเภทเรื่อง"
# มาตั้งแต่ระบบเดิมแล้ว — เก็บเป็นข้อความ ตอนนี้เปลี่ยนเป็น id)
# =========================================================
class ComplaintMaster(Base):
    __tablename__ = "complaint_master"

    id = Column(Integer, primary_key=True, index=True)

    department_id = Column(
        Integer,
        ForeignKey("departments.department_id"),
        nullable=False,
        index=True,
    )

    # ชื่อประเภทเรื่องที่ผู้ใช้เห็น เช่น "การซ่อมบำรุง / อุปกรณ์"
    name = Column(String(255), nullable=False)

    # ชื่อไอคอน lucide-react ที่ฝั่งหน้าจอ map เป็นคอมโพเนนต์ เช่น "Wrench"
    # เก็บเป็นชื่อไม่ใช่ SVG — ฝั่ง backend ไม่ต้องรู้จักชุดไอคอนของ frontend
    icon = Column(String(50))

    sort_order = Column(Integer, default=0, nullable=False)

    # ปิดการใช้งานแทนการลบ เมื่อประเภทนั้นถูกอ้างถึงโดยคำร้องเก่าอยู่แล้ว
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    department = relationship("Department")

    __table_args__ = (
        # ชื่อซ้ำได้ข้ามหน่วยงาน (เช่น "การดูแลรถและอุปกรณ์" มีได้ทั้ง 3 และ 17)
        # แต่ห้ามซ้ำภายในหน่วยงานเดียวกัน ไม่งั้นดรอปดาวน์จะมีสองบรรทัดที่อ่านเหมือนกัน
        UniqueConstraint("department_id", "name", name="uq_complaint_master_dept_name"),
    )
