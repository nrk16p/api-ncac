from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Optional
from datetime import datetime

from database import get_db
from models.complaint_master import ComplaintMaster
from models.complaint import DriverComplaint
from schemas.complaint_master import (
    ComplaintMasterCreate,
    ComplaintMasterUpdate,
    ComplaintMasterResponse,
)

router = APIRouter(prefix="/complaint-masters", tags=["Complaint Master"])


# =========================================================
# HELPERS
# =========================================================
def ensure_department_exists(db: Session, department_id: int):
    """
    department_id เป็น FK ไป departments.department_id — ถ้าส่งค่าที่ไม่มีจริง
    IntegrityError จะหลุดออกไปเป็น 500 ซึ่งฝั่งเรียกอ่านแล้วไม่รู้ว่าผิดตรงไหน
    เช็กก่อนแล้วตอบ 400 พร้อมเหตุผล (แพตเทิร์นเดียวกับ ensure_employee_exists)
    """
    exists = db.execute(
        text("SELECT 1 FROM departments WHERE department_id = :did LIMIT 1"),
        {"did": department_id},
    ).first()

    if not exists:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown department_id '{department_id}'",
        )


def ensure_name_unique(
    db: Session,
    department_id: int,
    name: str,
    exclude_id: Optional[int] = None,
):
    """
    กันชื่อซ้ำภายในหน่วยงานเดียวกัน — เช็กก่อนเพื่อให้ได้ 400 ที่อ่านรู้เรื่อง
    แทน 500 จาก unique constraint uq_complaint_master_dept_name
    """
    q = db.query(ComplaintMaster).filter(
        ComplaintMaster.department_id == department_id,
        ComplaintMaster.name == name,
    )

    if exclude_id is not None:
        q = q.filter(ComplaintMaster.id != exclude_id)

    if q.first():
        raise HTTPException(
            status_code=400,
            detail=f"Complaint type '{name}' already exists in department {department_id}",
        )


def usage_count(db: Session, master_id: int) -> int:
    """จำนวนคำร้องที่ชี้มาที่ประเภทนี้ — รวมคำร้องที่ถูกลบแบบ soft ด้วย
    เพราะ FK ยังผูกอยู่จริงไม่ว่าคำร้องจะโดนซ่อนจากคิวงานหรือไม่"""
    return (
        db.query(DriverComplaint)
        .filter(DriverComplaint.problem == master_id)
        .count()
    )


# =========================================================
# READ
# =========================================================
@router.get("/", response_model=List[ComplaintMasterResponse])
def list_complaint_masters(
    department_id: Optional[int] = None,
    include_inactive: bool = Query(
        False,
        description="รวมประเภทที่ปิดใช้งานแล้วด้วย — หน้าจอตั้งค่าเปิดใช้ ฟอร์มกรอกคำร้องไม่ต้อง",
    ),
    db: Session = Depends(get_db),
):
    q = db.query(ComplaintMaster)

    if department_id is not None:
        q = q.filter(ComplaintMaster.department_id == department_id)

    if not include_inactive:
        q = q.filter(ComplaintMaster.is_active == True)  # noqa: E712

    return q.order_by(
        ComplaintMaster.department_id,
        ComplaintMaster.sort_order,
        ComplaintMaster.id,
    ).all()


@router.get("/{id}", response_model=ComplaintMasterResponse)
def get_complaint_master(id: int, db: Session = Depends(get_db)):
    obj = db.get(ComplaintMaster, id)
    if not obj:
        raise HTTPException(status_code=404, detail="Complaint master not found")
    return obj


# =========================================================
# CREATE
# =========================================================
@router.post("/", response_model=ComplaintMasterResponse, status_code=201)
def create_complaint_master(
    payload: ComplaintMasterCreate,
    db: Session = Depends(get_db),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name must not be blank")

    ensure_department_exists(db, payload.department_id)
    ensure_name_unique(db, payload.department_id, name)

    obj = ComplaintMaster(
        department_id=payload.department_id,
        name=name,
        icon=payload.icon,
        sort_order=payload.sort_order,
        is_active=payload.is_active,
    )

    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


# =========================================================
# UPDATE
# =========================================================
@router.put("/{id}", response_model=ComplaintMasterResponse)
def update_complaint_master(
    id: int,
    payload: ComplaintMasterUpdate,
    db: Session = Depends(get_db),
):
    obj = db.get(ComplaintMaster, id)
    if not obj:
        raise HTTPException(status_code=404, detail="Complaint master not found")

    data = payload.dict(exclude_unset=True)

    if "name" in data:
        data["name"] = (data["name"] or "").strip()
        if not data["name"]:
            raise HTTPException(status_code=400, detail="name must not be blank")

    if "department_id" in data:
        ensure_department_exists(db, data["department_id"])

    # ย้ายหน่วยงานหรือเปลี่ยนชื่อ ต้องไม่ไปชนของที่มีอยู่แล้วในหน่วยงานปลายทาง
    target_department = data.get("department_id", obj.department_id)
    target_name = data.get("name", obj.name)
    if "department_id" in data or "name" in data:
        ensure_name_unique(db, target_department, target_name, exclude_id=obj.id)

    for key, value in data.items():
        setattr(obj, key, value)

    obj.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(obj)
    return obj


# =========================================================
# DELETE
#
# ลบจริงได้เฉพาะประเภทที่ยังไม่มีคำร้องไหนอ้างถึง — ที่ถูกใช้ไปแล้วต้องปิดใช้งาน
# (is_active = false) แทน ไม่งั้นคำร้องเก่าจะเหลือ problem ที่ชี้ไปยังแถวที่หายไป
# แล้วประวัติอ่านไม่ออกย้อนหลัง
# =========================================================
@router.delete("/{id}")
def delete_complaint_master(id: int, db: Session = Depends(get_db)):
    obj = db.get(ComplaintMaster, id)
    if not obj:
        raise HTTPException(status_code=404, detail="Complaint master not found")

    used = usage_count(db, id)
    if used:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Complaint type is used by {used} complaint(s) — "
                "set is_active=false instead of deleting"
            ),
        )

    db.delete(obj)
    db.commit()
    return {"message": f"Complaint master {id} deleted"}
