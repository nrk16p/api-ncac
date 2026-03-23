from fastapi import APIRouter

from .task import router as task_router
from .driver import router as driver_router
from .drug_test import router as drug_router
from .ppe import router as ppe_router
from .vehicle import router as vehicle_router
from .safety_talk import router as safety_talk_router
router = APIRouter(prefix="/inspection", tags=["inspection"])

router.include_router(task_router)
router.include_router(driver_router)
router.include_router(drug_router)
router.include_router(ppe_router)
router.include_router(vehicle_router)
router.include_router(safety_talk_router)