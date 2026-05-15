from fastapi import APIRouter

from .booking import router as booking_router
from .calendar import router as calendar_router
from .monthly_quota import router as monthly_quota_router
from .plant import router as plant_router
from .system_status import router as system_status_router

router = APIRouter(prefix="/leave-booking")

router.include_router(monthly_quota_router, tags=["Monthly Quota"])
router.include_router(booking_router, tags=["Booking"])
router.include_router(calendar_router, tags=["Calendar"])
router.include_router(plant_router, tags=["Plant"])
router.include_router(system_status_router, tags=["System Status"])