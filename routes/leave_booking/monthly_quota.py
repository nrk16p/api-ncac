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
    """
    Quota รวมทั้ง fleet ต่อ 1 วัน
    Example:
    total_driver = 200
    percentage = 0.03
    daily_quota = floor(200 * 0.03) = 6
    """
    value = math.floor(total_driver * percentage)
    return max(1, value)


def calculate_monthly_quota(total_driver: int, days: int, percentage: float) -> int:
    """
    Quota รวมทั้ง fleet ต่อเดือน
    """
    value = math.floor(total_driver * days * percentage)
    return max(1, value)


# ======================================================
# 🚀 CREATE Monthly Quota
# ======================================================
@router.post("/")
def create_monthly_quota(payload: dict, db: Session = Depends(get_db)):

    # ================================
    # ✅ Validate
    # ================================
    if payload.get("percentage") is None:
        raise HTTPException(status_code=400, detail="percentage is required")

    if payload["percentage"] <= 0 or payload["percentage"] > 1:
        raise HTTPException(
            status_code=400,
            detail="percentage must be between 0 and 1"
        )

    if payload.get("month") is None or payload["month"] < 1 or payload["month"] > 12:
        raise HTTPException(status_code=400, detail="invalid month")

    if payload.get("year") is None:
        raise HTTPException(status_code=400, detail="year is required")

    if payload.get("fleet") is None or str(payload["fleet"]).strip() == "":
        raise HTTPException(status_code=400, detail="fleet is required")

    if payload.get("total_driver") is None or payload["total_driver"] <= 0:
        raise HTTPException(status_code=400, detail="total_driver must be > 0")

    # ================================
    # 📅 Setup
    # ================================
    year = int(payload["year"])
    month = int(payload["month"])
    fleet = str(payload["fleet"]).strip()
    total_driver = int(payload["total_driver"])
    percentage = float(payload["percentage"])

    days = get_days_in_month(year, month)

    # ✅ This is fleet-level quota per day
    # Example: daily_quota = 6 means all plants combined can book max 6 per day
    daily_quota = calculate_daily_quota(total_driver, percentage)

    # ✅ This is fleet-level quota per month
    monthly_quota_limit = calculate_monthly_quota(
        total_driver=total_driver,
        days=days,
        percentage=percentage
    )

    # ✅ This is plant-level quota per day
    # New rule: each plant can have only 1 booking per day
    plant_daily_quota = 1

    # ================================
    # 📦 Monthly Quota create / reuse
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

            # ✅ quota รวมทั้ง fleet ต่อวัน
            daily_quota=daily_quota,

            # ✅ quota รวมทั้ง fleet ต่อเดือน
            monthly_quota_limit=monthly_quota_limit,

            is_latest=True
        )
        db.add(quota)
        db.flush()
    else:
        # ถ้ามี monthly quota อยู่แล้ว ใช้ค่าเดิม
        daily_quota = exists.daily_quota
        monthly_quota_limit = exists.monthly_quota_limit

    # ================================
    # 🏭 Get Plants
    # ================================
    plants = db.query(PlantMaster).filter(
        PlantMaster.fleet == fleet
    ).all()

    if not plants:
        raise HTTPException(
            status_code=400,
            detail="No plants found for this fleet"
        )

    # ================================
    # 📅 Date range
    # ================================
    start_date = date(year, month, 1)
    end_date = date(year, month, days)

    # ================================
    # 🔎 Existing Daily Quota
    # ================================
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

                    # ✅ UPDATED LOGIC
                    # 1 plant / 1 day = max 1 quota
                    # ไม่ใช้ daily_quota ตรงนี้แล้ว
                    quota=plant_daily_quota
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

            # quota รวมทั้ง fleet ต่อวัน
            "fleet_daily_quota": daily_quota,

            # quota ต่อ plant ต่อวัน
            "plant_daily_quota": plant_daily_quota,

            # quota รวมทั้ง fleet ต่อเดือน
            "monthly_quota_limit": monthly_quota_limit,

            "daily_quota_created": created_count,
            "daily_quota_skipped": skipped_count,
            "total_expected": len(plants) * days,
            "total_plants": len(plants),
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

    return {
        "status": "success",
        "data": data
    }


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
        raise HTTPException(status_code=404, detail="Quota not found")

    return {
        "status": "success",
        "data": data
    }


# ======================================================
# 🔄 UPDATE Monthly Quota Version
# ======================================================
@router.put("/")
def update_monthly_quota(payload: dict, db: Session = Depends(get_db)):

    # ================================
    # ✅ Validate
    # ================================
    required_fields = ["fleet", "year", "month", "total_driver", "percentage"]

    for field in required_fields:
        if payload.get(field) is None:
            raise HTTPException(
                status_code=400,
                detail=f"{field} is required"
            )

    if payload["percentage"] <= 0 or payload["percentage"] > 1:
        raise HTTPException(
            status_code=400,
            detail="percentage must be between 0 and 1"
        )

    if payload["month"] < 1 or payload["month"] > 12:
        raise HTTPException(status_code=400, detail="invalid month")

    if payload["total_driver"] <= 0:
        raise HTTPException(
            status_code=400,
            detail="total_driver must be > 0"
        )

    fleet = str(payload["fleet"]).strip()
    year = int(payload["year"])
    month = int(payload["month"])

    old = db.query(MonthlyLeaveQuota).filter(
        MonthlyLeaveQuota.fleet == fleet,
        MonthlyLeaveQuota.year == year,
        MonthlyLeaveQuota.month == month,
        MonthlyLeaveQuota.is_latest == True
    ).first()

    if not old:
        raise HTTPException(status_code=404, detail="Quota not found")

    # ================================
    # Mark old version as not latest
    # ================================
    old.is_latest = False
    db.flush()

    # ================================
    # Recalculate fleet-level quota
    # ================================
    days = get_days_in_month(year, month)

    daily_quota = calculate_daily_quota(
        total_driver=int(payload["total_driver"]),
        percentage=float(payload["percentage"])
    )

    monthly_quota_limit = calculate_monthly_quota(
        total_driver=int(payload["total_driver"]),
        days=days,
        percentage=float(payload["percentage"])
    )

    payload["fleet"] = fleet
    payload["year"] = year
    payload["month"] = month
    payload["daily_quota"] = daily_quota
    payload["monthly_quota_limit"] = monthly_quota_limit

    new_quota = MonthlyLeaveQuota(
        **payload,
        version=old.version + 1,
        is_latest=True
    )

    db.add(new_quota)

    # ======================================================
    # ✅ UPDATED LOGIC FOR EXISTING LeaveDailyQuota
    # ======================================================
    # ถ้ามี LeaveDailyQuota ของเดือนนี้อยู่แล้ว
    # ให้ update quota ราย plant เป็น 1
    # เพราะ rule ใหม่คือ 1 plant / 1 day = quota 1
    # ======================================================
    start_date = date(year, month, 1)
    end_date = date(year, month, days)

    daily_rows = db.query(LeaveDailyQuota).filter(
        LeaveDailyQuota.fleet == fleet,
        LeaveDailyQuota.date >= start_date,
        LeaveDailyQuota.date <= end_date
    ).all()

    updated_daily_quota_count = 0

    for row in daily_rows:
        if row.quota != 1:
            row.quota = 1
            updated_daily_quota_count += 1

    db.commit()

    return {
        "status": "success",
        "message": "Quota updated",
        "data": {
            "fleet": fleet,
            "year": year,
            "month": month,

            # quota รวมทั้ง fleet ต่อวัน
            "fleet_daily_quota": daily_quota,

            # quota ต่อ plant ต่อวัน
            "plant_daily_quota": 1,

            "monthly_quota_limit": monthly_quota_limit,
            "updated_daily_quota_count": updated_daily_quota_count
        }
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
        raise HTTPException(status_code=404, detail="Quota not found")

    quota.is_active = is_active
    db.commit()

    return {
        "status": "success",
        "is_active": is_active
    }