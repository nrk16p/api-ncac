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
from models.master_model import MasterDriver
from schemas.complaint import ComplaintCreate, ComplaintResponse, ReviewCreate,ComplaintOut
from datetime import datetime
from sqlalchemy import text, func
from typing import Optional ,List, Iterable


router = APIRouter(prefix="/complaints", tags=["Complaints"])


# =========================================================
# PRODUCTION SAFE TRACKING GENERATOR
# Format: DCYYYYMMXXXX
# Example: DC2026020001
# =========================================================
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
        selectinload(DriverComplaint.logs)   # 👈 ต้องมี
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

    if current_review.reviewer_employee_id != reviewer_employee_id:
        raise HTTPException(
            status_code=400,
            detail="Not current approval level"
        )

    # Approve
    current_review.status = ReviewStatus.APPROVED
    current_review.reviewed_at = datetime.utcnow()
    current_review.remark = remark

    # Log approval
    log = ComplaintLog(
        complaint_id=complaint.id,
        action="APPROVE",
        remark=remark
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

    if current_review.reviewer_employee_id != reviewer_employee_id:
        raise HTTPException(
            status_code=400,
            detail="Not current approval level"
        )

    # Mark current level as REJECTED
    current_review.status = ReviewStatus.REJECTED
    current_review.reviewed_at = datetime.utcnow()
    current_review.remark = remark

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

    # Log action
    log = ComplaintLog(
        complaint_id=complaint.id,
        action="REJECT",
        remark=remark
    )
    db.add(log)

    db.commit()

    return {"message": "Complaint rejected"}