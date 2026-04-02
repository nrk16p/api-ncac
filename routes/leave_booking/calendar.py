from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import date
import calendar as pycal
from collections import defaultdict

from database import get_db
from models.leave_booking.daily_quota import LeaveDailyQuota
from models.leave_booking.booking import DriverLeaveBooking

router = APIRouter(prefix="/calendar")


@router.get("")
def get_calendar(
    fleet: str,
    plant: str,
    driver_id: str,   # 👈 รับเป็น string
    year: int,
    month: int,
    db: Session = Depends(get_db)
):
    # =========================
    # 🔥 FIX: enforce type
    # =========================
    driver_id = str(driver_id)

    # =========================
    # 📅 date range
    # =========================
    days = pycal.monthrange(year, month)[1]

    start_date = date(year, month, 1)
    end_date = date(year, month, days)

    # =========================
    # 🧠 daily_quota
    # =========================
    rows = db.query(LeaveDailyQuota).filter(
        LeaveDailyQuota.fleet == fleet,
        LeaveDailyQuota.plant == plant,
        LeaveDailyQuota.date >= start_date,
        LeaveDailyQuota.date <= end_date
    ).all()

    # =========================
    # 🧠 booking (optimized select)
    # =========================
    bookings = db.query(
        DriverLeaveBooking.leave_date,
        DriverLeaveBooking.driver_id,
        DriverLeaveBooking.status
    ).filter(
        DriverLeaveBooking.fleet == fleet,
        DriverLeaveBooking.plant == plant,
        DriverLeaveBooking.leave_date >= start_date,
        DriverLeaveBooking.leave_date <= end_date,
        DriverLeaveBooking.status.in_(["pending", "approve"])
    ).all()

    # =========================
    # 🧠 group
    # =========================
    booking_map = defaultdict(list)
    driver_map = {}

    for leave_date, b_driver_id, status in bookings:
        booking_map[leave_date].append(status)

        # 🔥 FIX: compare safely
        if str(b_driver_id) == driver_id:
            driver_map[leave_date] = status

    # =========================
    # 🧠 build response
    # =========================
    result = []

    for r in rows:
        used = len(booking_map[r.date])
        remaining = max(0, r.quota - used)

        result.append({
            "date": r.date,
            "quota": r.quota,
            "used": used,
            "remaining": remaining,
            "is_full": used >= r.quota,
            "driver_booking": driver_map.get(r.date)  # None / pending / approve
        })

    return {
        "status": "success",
        "data": result
    }