from fastapi import APIRouter, Depends, Query
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
    year: int,
    month: int,

    # optional filters
    fleet: str | None = Query(default=None),
    plant: str | None = Query(default=None),

    # optional: ถ้าส่งมา จะแสดง driver_booking ของคนนั้น
    driver_id: str | None = Query(default=None),

    db: Session = Depends(get_db)
):
    # =========================
    # Normalize input
    # =========================
    fleet = fleet.strip() if fleet else None
    plant = plant.strip() if plant else None
    driver_id = str(driver_id).strip() if driver_id else None

    # =========================
    # Date range
    # =========================
    days = pycal.monthrange(year, month)[1]

    start_date = date(year, month, 1)
    end_date = date(year, month, days)

    # =========================
    # Daily quota query
    # =========================
    quota_query = db.query(LeaveDailyQuota).filter(
        LeaveDailyQuota.date >= start_date,
        LeaveDailyQuota.date <= end_date
    )

    if fleet:
        quota_query = quota_query.filter(LeaveDailyQuota.fleet == fleet)

    if plant:
        quota_query = quota_query.filter(LeaveDailyQuota.plant == plant)

    quota_rows = quota_query.order_by(
        LeaveDailyQuota.date.asc(),
        LeaveDailyQuota.fleet.asc(),
        LeaveDailyQuota.plant.asc()
    ).all()

    # =========================
    # Booking query
    # =========================
    booking_query = db.query(
        DriverLeaveBooking.booking_id,
        DriverLeaveBooking.leave_date,
        DriverLeaveBooking.fleet,
        DriverLeaveBooking.plant,
        DriverLeaveBooking.driver_id,
        DriverLeaveBooking.status,
        DriverLeaveBooking.leave_type,
        DriverLeaveBooking.remark
    ).filter(
        DriverLeaveBooking.leave_date >= start_date,
        DriverLeaveBooking.leave_date <= end_date,
        DriverLeaveBooking.status.in_(["pending", "approve"])
    )

    if fleet:
        booking_query = booking_query.filter(DriverLeaveBooking.fleet == fleet)

    if plant:
        booking_query = booking_query.filter(DriverLeaveBooking.plant == plant)

    bookings = booking_query.all()

    # =========================
    # Group booking by date + fleet + plant
    # =========================
    booking_map = defaultdict(list)
    driver_map = {}

    for b in bookings:
        key = (b.leave_date, b.fleet, b.plant)

        booking_item = {
            "booking_id": b.booking_id,
            "driver_id": b.driver_id,
            "status": b.status,
            "leave_type": b.leave_type,
            "remark": b.remark
        }

        booking_map[key].append(booking_item)

        if driver_id and str(b.driver_id) == driver_id:
            driver_map[key] = booking_item

    # =========================
    # Build response
    # =========================
    result = []

    for r in quota_rows:
        key = (r.date, r.fleet, r.plant)

        bookings_on_day = booking_map[key]
        used = len(bookings_on_day)
        remaining = max(0, r.quota - used)

        item = {
            "date": r.date,
            "fleet": r.fleet,
            "plant": r.plant,
            "quota": r.quota,
            "used": used,
            "remaining": remaining,
            "is_full": used >= r.quota,
            "bookings": bookings_on_day
        }

        # ถ้ามี driver_id ค่อยเพิ่ม driver_booking
        if driver_id:
            item["driver_booking"] = driver_map.get(key)

        result.append(item)

    return {
        "status": "success",
        "filters": {
            "year": year,
            "month": month,
            "fleet": fleet,
            "plant": plant,
            "driver_id": driver_id
        },
        "data": result
    }