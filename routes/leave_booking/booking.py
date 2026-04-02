from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date, datetime

from database import get_db
from models.leave_booking.booking import DriverLeaveBooking
from models.leave_booking.daily_quota import LeaveDailyQuota
from models.leave_booking.blackout import LeaveBlackout

router = APIRouter(prefix="/booking")


# ======================================================
# 🧠 Helper: parse date safely
# ======================================================
def parse_date(d):
    if isinstance(d, date):
        return d
    try:
        return datetime.strptime(d, "%Y-%m-%d").date()
    except:
        raise HTTPException(400, "invalid date format (use YYYY-MM-DD)")


# ======================================================
# 🧠 Helper: blackout
# ======================================================
def is_blackout(db, fleet, plant, d):
    return db.query(LeaveBlackout).filter(
        LeaveBlackout.fleet == fleet,
        (LeaveBlackout.plant == plant) | (LeaveBlackout.plant.is_(None)),
        LeaveBlackout.start_date <= d,
        LeaveBlackout.end_date >= d
    ).first()


# ======================================================
# 🚀 CREATE BOOKING
# ======================================================
@router.post("/booking")
def create_booking(payload: dict, db: Session = Depends(get_db)):

    # =========================
    # 🔥 FIX: enforce type
    # =========================
    fleet = str(payload.get("fleet"))
    plant = str(payload.get("plant"))
    driver_id = str(payload.get("driver_id"))   # 👈 สำคัญมาก
    d = parse_date(payload.get("leave_date"))   # 👈 สำคัญมาก

    if not fleet or not plant or not driver_id:
        raise HTTPException(400, "missing required fields")

    # =========================
    # 🚫 blackout
    # =========================
    if is_blackout(db, fleet, plant, d):
        raise HTTPException(400, "blackout")

    # =========================
    # 📊 quota
    # =========================
    quota = db.query(LeaveDailyQuota).filter(
        LeaveDailyQuota.fleet == fleet,
        LeaveDailyQuota.plant == plant,
        LeaveDailyQuota.date == d
    ).first()

    if not quota:
        raise HTTPException(400, "no quota")

    used = db.query(func.count(DriverLeaveBooking.booking_id)).filter(
        DriverLeaveBooking.fleet == fleet,
        DriverLeaveBooking.plant == plant,
        DriverLeaveBooking.leave_date == d,
        DriverLeaveBooking.status.in_(["pending", "approve"])
    ).scalar()

    if used >= quota.quota:
        raise HTTPException(400, "quota full")

    # =========================
    # 🔁 duplicate check
    # =========================
    exists = db.query(DriverLeaveBooking).filter(
        DriverLeaveBooking.driver_id == driver_id,   # 👈 string vs string
        DriverLeaveBooking.leave_date == d
    ).first()

    if exists:
        raise HTTPException(400, "already booked")

    # =========================
    # 💾 create
    # =========================
    b = DriverLeaveBooking(
        fleet=fleet,
        plant=plant,
        driver_id=driver_id,
        leave_date=d,
        status=payload.get("status", "pending"),
        leave_type=payload.get("leave_type"),
        remark=payload.get("remark")
    )

    db.add(b)
    db.commit()

    return {"status": "success"}