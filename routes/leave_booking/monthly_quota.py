from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import date
import calendar as pycal
import math

from database import get_db

from models.leave_booking.monthly_quota import MonthlyLeaveQuota
from models.leave_booking.daily_quota import LeaveDailyQuota
from models.master.plant import PlantMaster

router = APIRouter(prefix="/monthly-quota")
# ======================================================
# 🧠 Helper
# ======================================================
def get_days_in_month(year: int, month: int) -> int:
    return pycal.monthrange(year, month)[1]


def calculate_daily_quota(total_driver: int, percentage: float) -> int:
    value = math.floor(total_driver * percentage)
    return max(1, value)


def calculate_monthly_quota(total_driver: int, days: int, percentage: float) -> int:
    value = math.floor(total_driver * days * percentage)
    return max(1, value)


# ======================================================
# 🚀 CREATE (percentage-based)
# ======================================================
# ======================================================
# 🚀 CREATE (percentage-based)
# ======================================================
@router.post("/")
def create_monthly_quota(payload: dict, db: Session = Depends(get_db)):

    # ================================
    # ✅ Validate
    # ================================
    if payload.get("percentage") is None:
        raise HTTPException(400, "percentage is required")

    if payload["percentage"] <= 0 or payload["percentage"] > 1:
        raise HTTPException(400, "percentage must be between 0 and 1")

    if payload["month"] < 1 or payload["month"] > 12:
        raise HTTPException(400, "invalid month")

    if payload["total_driver"] <= 0:
        raise HTTPException(400, "total_driver must be > 0")

    # ================================
    # 📅 Setup
    # ================================
    year = payload["year"]
    month = payload["month"]
    fleet = payload["fleet"]
    total_driver = payload["total_driver"]
    percentage = payload["percentage"]

    days = get_days_in_month(year, month)

    daily_quota = calculate_daily_quota(total_driver, percentage)
    monthly_quota_limit = calculate_monthly_quota(total_driver, days, percentage)

    # ================================
    # 📦 Monthly Quota (create / reuse)
    # ================================
    exists = db.query(MonthlyLeaveQuota).filter(
        MonthlyLeaveQuota.fleet == fleet,
        MonthlyLeaveQuota.year == year,
        MonthlyLeaveQuota.month == month,
        MonthlyLeaveQuota.is_latest == True
    ).first()

    if not exists:
        quota = MonthlyLeaveQuota(
            fleet=fleet,
            year=year,
            month=month,
            percentage=percentage,
            total_driver=total_driver,
            daily_quota=daily_quota,
            monthly_quota_limit=monthly_quota_limit,
            is_latest=True
        )
        db.add(quota)
        db.flush()
    else:
        daily_quota = exists.daily_quota
        monthly_quota_limit = exists.monthly_quota_limit

    # ================================
    # 🏭 Get Plants
    # ================================
    plants = db.query(PlantMaster).filter(
        PlantMaster.fleet == fleet
    ).all()

    if not plants:
        raise HTTPException(400, "No plants found for this fleet")

    # ================================
    # 🔥 FIX: filter existing by month
    # ================================
    start_date = date(year, month, 1)
    end_date = date(year, month, days)

    existing = db.query(LeaveDailyQuota).filter(
        LeaveDailyQuota.fleet == fleet,
        LeaveDailyQuota.date >= start_date,
        LeaveDailyQuota.date <= end_date
    ).all()

    existing_set = {
        (e.plant, e.date) for e in existing
    }

    # ================================
    # 🚀 Create Daily Quota
    # ================================
    created_count = 0
    skipped_count = 0

    for plant in plants:
        for d in range(1, days + 1):
            target_date = date(year, month, d)

            if (plant.plant_code, target_date) in existing_set:
                skipped_count += 1
                continue

            db.add(
                LeaveDailyQuota(
                    fleet=fleet,
                    plant=plant.plant_code,
                    date=target_date,
                    quota=daily_quota
                )
            )
            created_count += 1

    db.commit()

    # ================================
    # 📊 Response
    # ================================
    return {
        "status": "success",
        "message": "Monthly quota processed",
        "data": {
            "fleet": fleet,
            "year": year,
            "month": month,
            "days_in_month": days,
            "daily_quota": daily_quota,
            "monthly_quota_limit": monthly_quota_limit,
            "daily_quota_created": created_count,
            "daily_quota_skipped": skipped_count,
            "total_expected": len(plants) * days,
            "daily_status": "created" if created_count > 0 else "already_exists"
        }
    }
# ======================================================
# 📄 LIST
# ======================================================
@router.get("/")
def list_monthly_quota(
    fleet: str = Query(...),
    year: int = Query(...),
    month: int = Query(...),
    db: Session = Depends(get_db)
):
    data = db.query(MonthlyLeaveQuota).filter(
        MonthlyLeaveQuota.fleet == fleet,
        MonthlyLeaveQuota.year == year,
        MonthlyLeaveQuota.month == month,
        MonthlyLeaveQuota.is_latest == True
    ).all()

    return {"status": "success", "data": data}


# ======================================================
# 📄 DETAIL
# ======================================================
@router.get("/detail")
def get_monthly_quota(
    fleet: str,
    year: int,
    month: int,
    db: Session = Depends(get_db)
):
    data = db.query(MonthlyLeaveQuota).filter(
        MonthlyLeaveQuota.fleet == fleet,
        MonthlyLeaveQuota.year == year,
        MonthlyLeaveQuota.month == month,
        MonthlyLeaveQuota.is_latest == True
    ).first()

    if not data:
        raise HTTPException(404, "Quota not found")

    return {"status": "success", "data": data}


# ======================================================
# 🔄 UPDATE (versioning)
# ======================================================
@router.put("/")
def update_monthly_quota(payload: dict, db: Session = Depends(get_db)):

    old = db.query(MonthlyLeaveQuota).filter(
        MonthlyLeaveQuota.fleet == payload["fleet"],
        MonthlyLeaveQuota.year == payload["year"],
        MonthlyLeaveQuota.month == payload["month"],
        MonthlyLeaveQuota.is_latest == True
    ).first()

    if not old:
        raise HTTPException(404, "Quota not found")

    # mark old as not latest
    old.is_latest = False
    db.flush()

    # recalculate
    days = get_days_in_month(payload["year"], payload["month"])
    daily_quota = calculate_daily_quota(payload["total_driver"], payload["percentage"])
    monthly_quota_limit = calculate_monthly_quota(payload["total_driver"], days, payload["percentage"])

    payload["daily_quota"] = daily_quota
    payload["monthly_quota_limit"] = monthly_quota_limit

    new_quota = MonthlyLeaveQuota(
        **payload,
        version=old.version + 1,
        is_latest=True
    )

    db.add(new_quota)
    db.commit()

    return {
        "status": "success",
        "message": "Quota updated",
        "daily_quota": daily_quota
    }


# ======================================================
# 🔴 ACTIVATE / DEACTIVATE
# ======================================================
@router.patch("/status")
def toggle_status(
    fleet: str,
    year: int,
    month: int,
    is_active: bool,
    db: Session = Depends(get_db)
):
    quota = db.query(MonthlyLeaveQuota).filter(
        MonthlyLeaveQuota.fleet == fleet,
        MonthlyLeaveQuota.year == year,
        MonthlyLeaveQuota.month == month,
        MonthlyLeaveQuota.is_latest == True
    ).first()

    if not quota:
        raise HTTPException(404, "Quota not found")

    quota.is_active = is_active
    db.commit()

    return {"status": "success", "is_active": is_active}