from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date, datetime
import calendar

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
    except Exception:
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
# 🧠 Helper: monthly leave limit per driver
# Rule:
# - month 31 days => max leave 4 days
# - month 30 days => max leave 3 days
# - month 29 days => max leave 2 days
# - month 28 days => max leave 1 day
# - month 27 days => max leave 0 day
# ======================================================
def get_driver_monthly_limit(year: int, month: int) -> int:
    days_in_month = calendar.monthrange(year, month)[1]
    return max(0, days_in_month - 27)


# ======================================================
# 🚀 CREATE BOOKING
# ======================================================
@router.post("/driver")
def create_booking(payload: dict, db: Session = Depends(get_db)):
    # =========================
    # 🔥 normalize input
    # =========================
    fleet = str(payload.get("fleet") or "").strip()
    plant = str(payload.get("plant") or "").strip()
    driver_id = str(payload.get("driver_id") or "").strip()
    d = parse_date(payload.get("leave_date"))

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
    ).scalar() or 0

    if used >= quota.quota:
        raise HTTPException(400, "quota full")

    # =========================
    # 🔁 duplicate check
    # same driver cannot book same day
    # =========================
    exists = db.query(DriverLeaveBooking).filter(
        DriverLeaveBooking.fleet == fleet,
        DriverLeaveBooking.plant == plant,
        DriverLeaveBooking.driver_id == driver_id,
        DriverLeaveBooking.leave_date == d
    ).first()

    if exists:
        raise HTTPException(400, "already booked")

    # =========================
    # 🚧 monthly driver limit
    # each driver must work at least 27 days/month
    # =========================
    year = d.year
    month = d.month
    month_days = calendar.monthrange(year, month)[1]
    month_start = date(year, month, 1)
    month_end = date(year, month, month_days)

    monthly_limit = get_driver_monthly_limit(year, month)

    current_used = db.query(func.count(DriverLeaveBooking.booking_id)).filter(
        DriverLeaveBooking.driver_id == driver_id,
        DriverLeaveBooking.leave_date >= month_start,
        DriverLeaveBooking.leave_date <= month_end,
        DriverLeaveBooking.status.in_(["pending", "approve"])
    ).scalar() or 0

    if current_used >= monthly_limit:
        raise HTTPException(
            400,
            f"monthly limit reached ({monthly_limit} days)"
        )

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
    db.refresh(b)

    return {
        "status": "success",
        "message": "booking created",
        "data": {
            "booking_id": b.booking_id,
            "fleet": b.fleet,
            "plant": b.plant,
            "driver_id": b.driver_id,
            "leave_date": b.leave_date,
            "status": b.status,
            "leave_type": b.leave_type,
            "remark": b.remark,
            "monthly_limit": monthly_limit,
            "current_used_after_create": current_used + 1,
            "remaining_monthly_quota": max(0, monthly_limit - (current_used + 1))
        }
    }


# ======================================================
# 📄 MY BOOKING
# ======================================================
@router.get("/my")
def get_my_booking(
    driver_id: str,
    year: int,
    month: int,
    db: Session = Depends(get_db)
):
    driver_id = str(driver_id).strip()

    month_days = calendar.monthrange(year, month)[1]
    start = date(year, month, 1)
    end = date(year, month, month_days)

    monthly_limit = get_driver_monthly_limit(year, month)

    data = db.query(DriverLeaveBooking).filter(
        DriverLeaveBooking.driver_id == driver_id,
        DriverLeaveBooking.leave_date >= start,
        DriverLeaveBooking.leave_date <= end
    ).order_by(DriverLeaveBooking.leave_date.asc()).all()

    current_used = db.query(func.count(DriverLeaveBooking.booking_id)).filter(
        DriverLeaveBooking.driver_id == driver_id,
        DriverLeaveBooking.leave_date >= start,
        DriverLeaveBooking.leave_date <= end,
        DriverLeaveBooking.status.in_(["pending", "approve"])
    ).scalar() or 0

    return {
        "status": "success",
        "data": data,
        "summary": {
            "driver_id": driver_id,
            "year": year,
            "month": month,
            "days_in_month": month_days,
            "monthly_limit": monthly_limit,
            "current_used": current_used,
            "remaining_monthly_quota": max(0, monthly_limit - current_used)
        }
    }

@router.get("/admin")
def get_admin_booking(
    year: int | None = None,
    month: int | None = None,
    fleet: str | None = None,
    plant: str | None = None,
    db: Session = Depends(get_db)
):
    # =========================
    # Normalize filters
    # =========================
    fleet = fleet.strip() if fleet else None
    plant = plant.strip() if plant else None

    query = db.query(DriverLeaveBooking)

    # =========================
    # Filter by year/month
    # =========================
    start = None
    end = None

    if year and month:
        month_days = calendar.monthrange(year, month)[1]
        start = date(year, month, 1)
        end = date(year, month, month_days)

        query = query.filter(
            DriverLeaveBooking.leave_date >= start,
            DriverLeaveBooking.leave_date <= end
        )

    elif year and not month:
        start = date(year, 1, 1)
        end = date(year, 12, 31)

        query = query.filter(
            DriverLeaveBooking.leave_date >= start,
            DriverLeaveBooking.leave_date <= end
        )

    elif month and not year:
        raise HTTPException(
            status_code=400,
            detail="year is required when month is provided"
        )

    # =========================
    # Optional fleet / plant filter
    # =========================
    if fleet:
        query = query.filter(DriverLeaveBooking.fleet == fleet)

    if plant:
        query = query.filter(DriverLeaveBooking.plant == plant)

    # =========================
    # Get data
    # =========================
    data = query.order_by(
        DriverLeaveBooking.leave_date.asc(),
        DriverLeaveBooking.fleet.asc(),
        DriverLeaveBooking.plant.asc(),
        DriverLeaveBooking.driver_id.asc()
    ).all()

    # =========================
    # Summary
    # =========================
    total = len(data)
    pending = len([x for x in data if x.status == "pending"])
    approve = len([x for x in data if x.status == "approve"])
    reject = len([x for x in data if x.status == "reject"])
    cancel = len([x for x in data if x.status == "cancel"])

    return {
        "status": "success",
        "filters": {
            "year": year,
            "month": month,
            "fleet": fleet,
            "plant": plant
        },
        "summary": {
            "total": total,
            "pending": pending,
            "approve": approve,
            "reject": reject,
            "cancel": cancel
        },
        "data": data
    }

@router.put("/{booking_id}/status")
def update_booking_status(
    booking_id: int,
    payload: dict,
    db: Session = Depends(get_db)
):
    # =========================
    # Normalize input
    # =========================
    new_status = str(payload.get("status") or "").strip().lower()
    admin_remark = payload.get("admin_remark")

    allowed_status = ["pending", "approve", "reject", "cancel"]

    if new_status not in allowed_status:
        raise HTTPException(
            status_code=400,
            detail=f"invalid status. allowed: {allowed_status}"
        )

    # =========================
    # Find booking
    # =========================
    booking = db.query(DriverLeaveBooking).filter(
        DriverLeaveBooking.booking_id == booking_id
    ).first()

    if not booking:
        raise HTTPException(
            status_code=404,
            detail="booking not found"
        )

    # =========================
    # Update status
    # =========================
    booking.status = new_status

    # Optional: append admin remark into existing remark
    if admin_remark:
        old_remark = booking.remark or ""
        booking.remark = f"{old_remark}\n[ADMIN] {admin_remark}".strip()

    db.commit()
    db.refresh(booking)

    return {
        "status": "success",
        "message": "booking status updated",
        "data": {
            "booking_id": booking.booking_id,
            "fleet": booking.fleet,
            "plant": booking.plant,
            "driver_id": booking.driver_id,
            "leave_date": booking.leave_date,
            "status": booking.status,
            "leave_type": booking.leave_type,
            "remark": booking.remark
        }
    }