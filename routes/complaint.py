from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session,selectinload
from sqlalchemy.exc import IntegrityError
from database import get_db
from models.complaint import (
    DriverComplaint,
    ComplaintReview,
    ComplaintLog,
    ComplaintStatus,
    ReviewStatus
)
from models.complaint_master import ComplaintMaster
from models.master_model import MasterDriver
from models.user_model import User
from schemas.complaint import ComplaintCreate, ComplaintResponse, ReviewCreate,ComplaintOut
from datetime import datetime
from sqlalchemy import text, func
from typing import Optional ,List, Iterable
import os


router = APIRouter(prefix="/complaints", tags=["Complaints"])


# =========================================================
# PRODUCTION SAFE TRACKING GENERATOR
# Format: DCYYYYMMXXXX
# Example: DC2026020001
# =========================================================
# =========================================================
# ACTOR GUARD
#
# complaint_logs.action_by_employee_id มี FK ไป users.employee_id
# (constraint ชื่อ fk_log_user) ส่ง employee_id ที่ไม่มีอยู่จริงมา แถว log จะ insert
# ไม่ผ่าน แล้วทั้ง transaction ถูก rollback — เดิมหลุดออกไปเป็น 500 Internal Server
# Error ซึ่งฝั่งเรียกอ่านแล้วไม่รู้เลยว่าผิดตรงไหน จึงเช็กก่อนแล้วตอบ 400 พร้อมเหตุผล
#
# เช็กด้วย SQL ตรง ๆ เพื่อไม่ต้องผูกกับ model ของ users ในไฟล์นี้
# =========================================================
def ensure_employee_exists(db: Session, employee_id: str, field: str):

    exists = db.execute(
        text("SELECT 1 FROM users WHERE employee_id = :eid LIMIT 1"),
        {"eid": employee_id}
    ).first()

    if not exists:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown employee_id '{employee_id}' for {field}"
        )


# =========================================================
# COMPLAINT TYPE GUARD
#
# driver_complaints.problem = id ของ complaint_master ("ประเภทเรื่อง")
#
# นอกจากต้องมีอยู่จริงแล้ว **ต้องเป็นประเภทของหน่วยงานที่ถือคำร้องนั้นด้วย** —
# รายการประเภทเรื่องถูกจัดกลุ่มตามหน่วยงาน การจับคู่ข้ามหน่วยงานจะได้คำร้องที่
# ดรอปดาวน์ฝั่งหน้าจอหาป้ายไม่เจอ กลายเป็นช่องว่างทั้งที่ในฐานมีค่าอยู่
#
# `department_id` ที่ใช้เทียบคือค่า **หลังบันทึกรอบนี้** ไม่ใช่ค่าเดิมในฐาน
# เพราะการย้ายหน่วยงานกับเลือกประเภทเรื่องมาในคำขอเดียวกันได้
# =========================================================
def resolve_complaint_master(
    db: Session,
    master_id: Optional[int],
    department_id: Optional[int],
) -> Optional[ComplaintMaster]:

    if master_id is None:
        return None

    master = db.get(ComplaintMaster, master_id)
    if not master:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown complaint type id '{master_id}'",
        )

    if department_id is not None and master.department_id != department_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Complaint type '{master.name}' belongs to department "
                f"{master.department_id}, not {department_id}"
            ),
        )

    return master


# =========================================================
# ผู้อนุมัติแทน (approve / reject on behalf)
#
# ปกติอนุมัติได้เฉพาะคนที่ถูกตั้งเป็นผู้อนุมัติของระดับที่ค้างอยู่เท่านั้น —
# แต่ต้องมีทางเดินเรื่องต่อเมื่อผู้อนุมัติตัวจริงลา / ลาออก / ติดต่อไม่ได้
# ไม่งั้นคำร้องค้างอยู่ที่ PENDING_REVIEW ถาวรโดยไม่มีใครปลดได้เลย
#
# ตั้งรายชื่อผ่าน env `SUPER_APPROVER_IDS` (คั่นด้วยคอมมา) เปลี่ยนตัวได้โดย
# ไม่ต้อง deploy ใหม่ · ค่าเริ่มต้นคือ 680043 ตามที่เจ้าของงานกำหนด (13 ส.ค. 2026)
#
# **ร่องรอยต้องไม่โกหก** — แถวใน complaint_reviews ยังเป็นชื่อผู้อนุมัติตัวจริง
# (เขาคือคนที่ถูกมอบหมายไว้ ข้อเท็จจริงนั้นไม่เปลี่ยน) แต่หมายเหตุจะถูกต่อท้าย
# ว่าใครกดแทน และ complaint_logs.action_by_employee_id บันทึก "คนที่กดจริง"
# เสมอ จึงไล่ย้อนได้ว่าใครเป็นคนตัดสิน
# =========================================================
def super_approver_ids() -> set:

    raw = os.getenv("SUPER_APPROVER_IDS", "680043")
    return {v.strip() for v in raw.split(",") if v.strip()}


def authorize_reviewer(
    db: Session,
    current_review: ComplaintReview,
    actor_employee_id: str,
) -> bool:
    """
    ตรวจสิทธิ์ตัดสินคำร้องของระดับที่ค้างอยู่ — คืน True ถ้าเป็นการทำแทน

    โยน 400 ข้อความเดิม ("Not current approval level") เมื่อไม่มีสิทธิ์ เพื่อให้
    ผู้เรียกเดิมที่ดักข้อความนี้อยู่ไม่ต้องแก้ตาม
    """
    if current_review.reviewer_employee_id == actor_employee_id:
        return False

    if actor_employee_id in super_approver_ids():
        # ผู้ทำแทนไม่ได้ผ่านการเทียบกับ current_review จึงยังไม่การันตีว่ามีตัวตน —
        # ต้องเช็กก่อน ไม่งั้น FK ของ complaint_logs จะพาทั้ง transaction ล้ม
        ensure_employee_exists(db, actor_employee_id, "reviewer_employee_id")
        return True

    raise HTTPException(
        status_code=400,
        detail="Not current approval level"
    )


def on_behalf_remark(remark: str, actor: str, assigned: str) -> str:
    """ต่อท้ายหมายเหตุให้เห็นในทุกหน้าจอที่อ่าน remark ว่าไม่ใช่เจ้าตัวเป็นคนกด"""
    return f"{remark} [ดำเนินการแทน {assigned} โดย {actor}]"


def generate_tracking(db: Session):

    now = datetime.utcnow()
    year_month = now.strftime("%Y%m")

    # Lock row (very important)
    counter = db.execute(
        text("""
            SELECT current_number
            FROM complaint_counters
            WHERE year_month = :ym
            FOR UPDATE
        """),
        {"ym": year_month}
    ).fetchone()

    if counter:
        new_number = counter[0] + 1
        db.execute(
            text("""
                UPDATE complaint_counters
                SET current_number = :num
                WHERE year_month = :ym
            """),
            {"num": new_number, "ym": year_month}
        )
    else:
        new_number = 1
        db.execute(
            text("""
                INSERT INTO complaint_counters (year_month, current_number)
                VALUES (:ym, :num)
            """),
            {"ym": year_month, "num": new_number}
        )

    return f"DC{year_month}{new_number:04d}"


# =========================================================
# DRIVER NAME RESOLVER
#
# masterdrivers.driver_id is TEXT (many values are not numeric, e.g.
# 'test-952777'), so match as trimmed text — never cast to integer.
# Unmatched ids simply resolve to None.
# =========================================================
def _driver_key(driver_id) -> Optional[str]:
    if driver_id is None:
        return None

    return str(driver_id).strip() or None


def build_driver_name_map(db: Session, driver_ids: Iterable) -> dict:
    """Resolve many driver_ids in a single query -> {driver_id: full name}."""

    keys = {k for k in (_driver_key(d) for d in driver_ids) if k is not None}

    if not keys:
        return {}

    trimmed_id = func.trim(MasterDriver.driver_id)

    rows = db.query(
        trimmed_id,
        MasterDriver.first_name,
        MasterDriver.last_name
    ).filter(
        trimmed_id.in_(keys)
    ).all()

    name_map = {}

    for driver_id, first_name, last_name in rows:
        full_name = " ".join(p for p in (first_name, last_name) if p).strip()
        name_map[driver_id] = full_name or None

    return name_map


def get_driver_name(db: Session, driver_id) -> Optional[str]:
    """Single-record convenience wrapper. Returns None when unmatched."""

    return build_driver_name_map(db, [driver_id]).get(_driver_key(driver_id))


# =========================================================
# CREATE COMPLAINT (Safe Retry Version)
# =========================================================
@router.post("/", response_model=ComplaintResponse)
def create_complaint(data: ComplaintCreate, db: Session = Depends(get_db)):

    tracking_no = generate_tracking(db)

    # resolve ชื่อคนขับตอนสร้าง — None ได้ถ้า map ไม่เจอ
    driver_name = get_driver_name(db, data.driver_id)

    complaint = DriverComplaint(
        tracking_no=tracking_no,
        driver_id=data.driver_id,
        driver_name=driver_name,
        subject=data.subject,
        detail=data.detail,
        complaint_type=data.complaint_type,
        complaint_details=data.complaint_details,
        complaint_url=data.complaint_url,
        status=ComplaintStatus.OPEN
    )

    db.add(complaint)
    db.commit()
    db.refresh(complaint)

    log = ComplaintLog(
        complaint_id=complaint.id,
        action="CREATE",
        remark="Complaint created"
    )

    db.add(log)
    db.commit()

    return complaint


# =========================================================
# GET BY TRACKING NO
# =========================================================
@router.get("/")
def get_complaints(
    driver_id: Optional[str] = None,
    status: Optional[ComplaintStatus] = None,
    department_id: Optional[int] = None,
    tracking_no: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db)
):

    query = db.query(DriverComplaint).options(
        selectinload(DriverComplaint.reviews),
        selectinload(DriverComplaint.logs),   # 👈 ต้องมี
        selectinload(DriverComplaint.problem_master)
    ).filter(
        DriverComplaint.is_deleted == False
    )

    if driver_id:
        query = query.filter(DriverComplaint.driver_id == driver_id)

    if status:
        query = query.filter(DriverComplaint.status == status)

    if department_id:
        query = query.filter(DriverComplaint.department_id == department_id)

    if tracking_no:
        query = query.filter(DriverComplaint.tracking_no == tracking_no)

    if start_date and end_date:
        start = datetime.fromisoformat(start_date)
        end = datetime.fromisoformat(end_date)
        query = query.filter(
            DriverComplaint.created_at.between(start, end)
        )

    complaints = query.order_by(
        DriverComplaint.created_at.desc()
    ).all()

    result = []

    for c in complaints:

        # driver_name เป็นคอลัมน์จริงแล้ว จึงติดมากับ loop นี้เลย
        complaint_dict = {
            column.name: getattr(c, column.name)
            for column in c.__table__.columns
        }

        # ✅ ประเภทเรื่อง — `problem` เป็น id ล้วน ๆ ส่งชื่อกับไอคอนไปด้วยเลย
        # ฝั่งหน้าจอจะได้วาดป้ายได้โดยไม่ต้องยิงถาม /complaint-masters ทีละรอบ
        # (null เมื่อยังไม่ได้เลือกประเภท — ผู้เรียกต้องรองรับค่าว่างเสมอ)
        complaint_dict["problem_master"] = (
            {
                "id": c.problem_master.id,
                "department_id": c.problem_master.department_id,
                "name": c.problem_master.name,
                "icon": c.problem_master.icon,
            }
            if c.problem_master
            else None
        )

        # ✅ reviews = approval only
        complaint_dict["reviews"] = [
            {
                col.name: getattr(r, col.name)
                for col in r.__table__.columns
            }
            for r in sorted(c.reviews, key=lambda x: x.level)
        ]

        # ✅ audit = CLOSE only
        complaint_dict["audit"] = [
            {
                "id": log.id,
                "level": log.action,
                "reviewer_employee_id": log.action_by_employee_id,
                "status": log.action,
                "remark": log.remark,
                "reviewed_at": log.created_at,
                "created_at": log.created_at
            }
            for log in c.logs
            if log.action == "CLOSE"
        ]

        result.append(complaint_dict)

    return result

# =========================================================
# ACTIVITY LOG
#
# ประวัติว่า "ใครทำอะไรกับคำร้องไหน" — อ่านจาก complaint_logs ตรง ๆ
#
# ต่างจาก GET / ตรงที่ **รวมคำร้องที่ถูกลบไปแล้วด้วย** (ไม่กรอง is_deleted)
# เพราะจุดประสงค์ทั้งหมดของหน้านี้คือให้เห็นว่ามีการลบเกิดขึ้น ถ้ากรองออก
# รายการ DELETE ก็จะหายไปพร้อมกับคำร้อง กลายเป็นล็อกที่ปิดบังเรื่องสำคัญที่สุด
#
# ต้องประกาศไว้ก่อน route ที่ขึ้นต้นด้วย path param เสมอ ไม่งั้น "logs"
# จะถูกจับเป็น tracking_no
#
# action_by_employee_id เป็น NULL ได้ (CREATE มาจากคนขับ ไม่ใช่ผู้ใช้ในระบบ ·
# ASSIGNED/RESUBMIT เกิดจาก PUT ซึ่งไม่ได้รับรหัสผู้ทำเข้ามา) ฝั่งหน้าจอต้อง
# รองรับค่าว่างเสมอ อย่าไปเดาว่าเป็นใคร
# =========================================================
@router.get("/logs")
def list_complaint_logs(
    action: Optional[str] = None,
    tracking_no: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db)
):

    limit = max(1, min(limit, 500))

    rows = (
        db.query(ComplaintLog, DriverComplaint, User)
        .join(DriverComplaint, ComplaintLog.complaint_id == DriverComplaint.id)
        .outerjoin(User, User.employee_id == ComplaintLog.action_by_employee_id)
        .order_by(ComplaintLog.created_at.desc(), ComplaintLog.id.desc())
    )

    if action:
        rows = rows.filter(ComplaintLog.action == action.upper())

    if tracking_no:
        rows = rows.filter(DriverComplaint.tracking_no == tracking_no)

    result = []

    for log, complaint, user in rows.limit(limit).all():

        actor_name = None
        if user:
            actor_name = " ".join(
                p for p in (user.firstname, user.lastname) if p
            ).strip() or None

        result.append({
            "id": log.id,
            "action": log.action,
            "remark": log.remark,
            "created_at": log.created_at,
            "tracking_no": complaint.tracking_no,
            "subject": complaint.subject,
            # บอกหน้าจอว่าคำร้องนี้ถูกลบไปแล้ว จะได้ไม่ลิงก์ไปหน้าที่เปิดไม่ได้
            "complaint_is_deleted": bool(complaint.is_deleted),
            "action_by_employee_id": log.action_by_employee_id,
            "action_by_name": actor_name,
        })

    return result


# =========================================================
# DEFINE REVIEWER
# =========================================================
@router.post("/{tracking_no}/define-reviewer")
def define_reviewer(
    tracking_no: str,
    data: ReviewCreate,
    db: Session = Depends(get_db)
):

    complaint = db.query(DriverComplaint).filter(
        DriverComplaint.tracking_no == tracking_no
    ).first()

    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")

    review = ComplaintReview(
        complaint_id=complaint.id,
        level=data.level,
        reviewer_employee_id=data.reviewer_employee_id
    )

    db.add(review)
    complaint.status = ComplaintStatus.PENDING_REVIEW
    db.commit()

    return {"message": "Reviewer assigned"}


# =========================================================
# APPROVE
# =========================================================
@router.post("/{tracking_no}/approve")
def approve_complaint(
    tracking_no: str,
    reviewer_employee_id: str,
    remark: str,
    db: Session = Depends(get_db)
):

    complaint = db.query(DriverComplaint).filter(
        DriverComplaint.tracking_no == tracking_no
    ).first()

    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")

    # Get current pending review (sequential enforcement)
    current_review = (
        db.query(ComplaintReview)
        .filter(
            ComplaintReview.complaint_id == complaint.id,
            ComplaintReview.status == ReviewStatus.PENDING
        )
        .order_by(ComplaintReview.level.asc())
        .first()
    )

    if not current_review:
        raise HTTPException(status_code=400, detail="No pending review")

    # ผู้อนุมัติตัวจริง หรือผู้อนุมัติแทนที่อยู่ใน SUPER_APPROVER_IDS
    on_behalf = authorize_reviewer(db, current_review, reviewer_employee_id)

    final_remark = (
        on_behalf_remark(remark, reviewer_employee_id, current_review.reviewer_employee_id)
        if on_behalf
        else remark
    )

    # Approve
    current_review.status = ReviewStatus.APPROVED
    current_review.reviewed_at = datetime.utcnow()
    current_review.remark = final_remark

    # Log approval — บันทึก "คนที่กดจริง" ไม่ใช่คนที่ถูกมอบหมาย ไม่งั้นล็อกจะบอกว่า
    # ผู้อนุมัติตัวจริงเป็นคนตัดสิน ทั้งที่เจ้าตัวไม่ได้แตะเลย
    log = ComplaintLog(
        complaint_id=complaint.id,
        action="APPROVE",
        remark=final_remark,
        action_by_employee_id=reviewer_employee_id
    )
    db.add(log)

    db.commit()

    # Check if more levels exist
    next_review = (
        db.query(ComplaintReview)
        .filter(
            ComplaintReview.complaint_id == complaint.id,
            ComplaintReview.status == ReviewStatus.PENDING
        )
        .first()
    )

    if not next_review:
        complaint.status = ComplaintStatus.READY_TO_CLOSE
        db.commit()

    return {"message": "Approved"}


# =========================================================
# CLOSE
# =========================================================
@router.post("/{tracking_no}/close")
def close_complaint(
    tracking_no: str,
    closer_employee_id: str,
    remark: str | None = None,
    db: Session = Depends(get_db)
):

    complaint = db.query(DriverComplaint).filter(
        DriverComplaint.tracking_no == tracking_no
    ).first()

    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")

    # ต้องเช็กก่อนแตะสถานะ — ไม่งั้น log insert พังท้ายสุดแล้ว rollback ทั้งชุด
    # ผู้ใช้เห็นแค่ 500 ทั้งที่สาเหตุจริงคือรหัสพนักงานไม่มีอยู่
    ensure_employee_exists(db, closer_employee_id, "closer_employee_id")

    # If no review defined, allow direct close
    review_exists = db.query(ComplaintReview).filter(
        ComplaintReview.complaint_id == complaint.id
    ).first()

    if review_exists and complaint.status != ComplaintStatus.READY_TO_CLOSE:
        raise HTTPException(
            status_code=400,
            detail="Not ready to close"
        )

    # Update status
    complaint.status = ComplaintStatus.CLOSED
    complaint.updated_at = datetime.utcnow()

    # Log close action
    log = ComplaintLog(
        complaint_id=complaint.id,
        action="CLOSE",
        remark=remark,
        action_by_employee_id=closer_employee_id,  # 🔥 important for audit
        created_at=datetime.utcnow()
    )

    db.add(log)
    db.commit()

    return {"message": "Complaint closed"}


# =========================================================
# DELETE (SOFT)
#
# ตั้ง is_deleted = True เท่านั้น — ไม่ลบแถวจริง เพราะ complaint_reviews และ
# complaint_logs ผูก FK ไว้แบบ CASCADE การลบจริงจะพาประวัติหายไปด้วยทั้งชุด
# GET / และ PUT /{tracking_no} กรอง is_deleted == False อยู่แล้ว คำร้องจึงหาย
# จากทุกหน้าจอทันทีโดยไม่ต้องแก้ที่อื่น
#
# มีไว้สำหรับเรื่องแจ้งผิด / แจ้งซ้ำ / เคสทดสอบ ซึ่งเกิดที่ต้นทางทั้งนั้น จึงล็อกไว้
# ที่ OPEN / ASSIGNED และต้องยังไม่มี review เลย — พอมีคนถูกตั้งเป็นผู้อนุมัติแล้ว
# เอกสารกลายเป็นหลักฐาน เส้นทางที่ถูกคือ /close ไม่ใช่ทำให้หายจากประวัติ
#
# กู้คืนยังไม่มี endpoint — ทำที่ฐานข้อมูลโดยตรง (UPDATE ... SET is_deleted = false)
# =========================================================
@router.delete("/{tracking_no}")
def delete_complaint(
    tracking_no: str,
    deleted_by_employee_id: str,
    remark: str | None = None,
    db: Session = Depends(get_db)
):

    complaint = db.query(DriverComplaint).filter(
        DriverComplaint.tracking_no == tracking_no,
        DriverComplaint.is_deleted == False
    ).first()

    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")

    ensure_employee_exists(db, deleted_by_employee_id, "deleted_by_employee_id")

    if complaint.status not in (
        ComplaintStatus.OPEN,
        ComplaintStatus.ASSIGNED
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot delete complaint in status {complaint.status.value} "
                "— close it instead"
            )
        )

    review_exists = db.query(ComplaintReview).filter(
        ComplaintReview.complaint_id == complaint.id
    ).first()

    if review_exists:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete complaint that already has a reviewer assigned "
                   "— close it instead"
        )

    complaint.is_deleted = True
    complaint.updated_at = datetime.utcnow()

    # เก็บร่องรอยไว้ในตารางเดิม — แถวยังอยู่ ประวัติจึงตามได้ว่าใครลบและเพราะอะไร
    log = ComplaintLog(
        complaint_id=complaint.id,
        action="DELETE",
        remark=remark,
        action_by_employee_id=deleted_by_employee_id,
        created_at=datetime.utcnow()
    )

    db.add(log)
    db.commit()

    return {
        "message": "Complaint deleted",
        "tracking_no": complaint.tracking_no,
        "is_deleted": True
    }


from schemas.complaint import ComplaintUpdate


# =========================================================
# UPDATE BY TRACKING NO
# =========================================================
@router.put("/{tracking_no}")
def update_complaint(
    tracking_no: str,
    data: ComplaintUpdate,
    db: Session = Depends(get_db)
):

    complaint = db.query(DriverComplaint).filter(
        DriverComplaint.tracking_no == tracking_no,
        DriverComplaint.is_deleted == False
    ).first()

    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")

    if complaint.status == ComplaintStatus.CLOSED:
        raise HTTPException(
            status_code=400,
            detail="Cannot update closed complaint"
        )

    update_data = data.dict(exclude_unset=True)

    # =====================================================
    # 0️⃣ ประเภทเรื่อง (problem) ต้องมีอยู่จริงและตรงหน่วยงาน
    #
    # ตรวจ **ก่อน** แตะอะไรในฐานเลย — ด่านนี้ต้องไม่ทิ้งการเปลี่ยนแปลงครึ่ง ๆ
    # กลาง ๆ ไว้เมื่อ payload ไม่ผ่าน
    # =====================================================
    if "problem" in update_data:
        target_department = update_data.get("department_id", complaint.department_id)
        resolve_complaint_master(db, update_data["problem"], target_department)

    # =====================================================
    # 1️⃣ If REJECTED → restart workflow
    # =====================================================
    if complaint.status == ComplaintStatus.REJECTED:

        db.query(ComplaintReview).filter(
            ComplaintReview.complaint_id == complaint.id
        ).delete()

        complaint.status = ComplaintStatus.ASSIGNED

        db.add(
            ComplaintLog(
                complaint_id=complaint.id,
                action="RESUBMIT",
                remark="Complaint edited after rejection and workflow restarted"
            )
        )

    # =====================================================
    # 2️⃣ If department_id updated → auto move to ASSIGNED
    # =====================================================
    if "department_id" in update_data:

        complaint.status = ComplaintStatus.ASSIGNED

        db.add(
            ComplaintLog(
                complaint_id=complaint.id,
                action="ASSIGNED",
                remark="Department changed — status auto set to ASSIGNED"
            )
        )

    # =====================================================
    # Apply field updates
    # =====================================================
    for key, value in update_data.items():
        setattr(complaint, key, value)

    # เปลี่ยนคนขับ → ชื่อเดิมใช้ไม่ได้แล้ว ต้อง resolve ใหม่
    if "driver_id" in update_data:
        complaint.driver_name = get_driver_name(db, complaint.driver_id)

    # ย้ายหน่วยงานโดยไม่ได้ส่งประเภทเรื่องมาด้วย → ประเภทเดิมเป็นของหน่วยงานเก่า
    # ล้างทิ้งเลย ดีกว่าปล่อยให้คำร้องถือประเภทที่ไม่มีในรายการของเจ้าของงานคนใหม่
    if "department_id" in update_data and "problem" not in update_data:
        if complaint.problem is not None:
            master = db.get(ComplaintMaster, complaint.problem)
            if master and master.department_id != complaint.department_id:
                complaint.problem = None

    complaint.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(complaint)

    return complaint

# =========================================================
# GET CURRENT APPROVAL LEVEL
# =========================================================
@router.get("/{tracking_no}/current-approval")
def get_current_approval(tracking_no: str, db: Session = Depends(get_db)):

    complaint = db.query(DriverComplaint).filter(
        DriverComplaint.tracking_no == tracking_no
    ).first()

    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")

    current_review = (
        db.query(ComplaintReview)
        .filter(
            ComplaintReview.complaint_id == complaint.id,
            ComplaintReview.status == ReviewStatus.PENDING
        )
        .order_by(ComplaintReview.level.asc())
        .first()
    )

    if not current_review:
        return {
            "message": "No pending approval",
            "status": complaint.status
        }

    return {
        "tracking_no": complaint.tracking_no,
        "current_level": current_review.level,
        "reviewer_employee_id": current_review.reviewer_employee_id,
        "complaint_status": complaint.status
    }

@router.post("/{tracking_no}/reject")
def reject_complaint(
    tracking_no: str,
    reviewer_employee_id: str,
    remark: str,
    db: Session = Depends(get_db)
):

    complaint = db.query(DriverComplaint).filter(
        DriverComplaint.tracking_no == tracking_no
    ).first()

    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")

    current_review = (
        db.query(ComplaintReview)
        .filter(
            ComplaintReview.complaint_id == complaint.id,
            ComplaintReview.status == ReviewStatus.PENDING
        )
        .order_by(ComplaintReview.level.asc())
        .first()
    )

    if not current_review:
        raise HTTPException(status_code=400, detail="No pending review")

    # ผู้อนุมัติตัวจริง หรือผู้อนุมัติแทนที่อยู่ใน SUPER_APPROVER_IDS
    on_behalf = authorize_reviewer(db, current_review, reviewer_employee_id)

    final_remark = (
        on_behalf_remark(remark, reviewer_employee_id, current_review.reviewer_employee_id)
        if on_behalf
        else remark
    )

    # Mark current level as REJECTED
    current_review.status = ReviewStatus.REJECTED
    current_review.reviewed_at = datetime.utcnow()
    current_review.remark = final_remark

    # 🔥 Cancel all remaining pending levels
    db.query(ComplaintReview).filter(
        ComplaintReview.complaint_id == complaint.id,
        ComplaintReview.status == ReviewStatus.PENDING
    ).update(
        {ComplaintReview.status: ReviewStatus.CANCELLED},
        synchronize_session=False
    )

    # Update complaint status
    complaint.status = ComplaintStatus.REJECTED

    # Log action — บันทึกผู้ปฏิเสธด้วย เหตุผลเดียวกับ APPROVE
    log = ComplaintLog(
        complaint_id=complaint.id,
        action="REJECT",
        remark=final_remark,
        action_by_employee_id=reviewer_employee_id
    )
    db.add(log)

    db.commit()

    return {"message": "Complaint rejected"}