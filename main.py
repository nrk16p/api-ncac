from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.middleware.cors import CORSMiddleware
import asyncio

from database import engine, Base
import models

# Create tables (dev convenience)
#
# ห้ามปล่อยให้ exception หลุดออกจากบรรทัดนี้: มันรันตอน import module ก่อน FastAPI
# จะถูกสร้างด้วยซ้ำ ถ้า DB ต่อไม่ได้ process จะตายด้วย status 1 → Render restart วน →
# ไม่มีพอร์ตให้สแกน → deploy ล้มด้วยข้อความ "No open ports detected" ที่ชี้ไปผิดทาง
# (เจอจริง 04/09/2026: คลัสเตอร์ DigitalOcean max_connections=25 เต็ม เพราะ DBeaver
# ค้าง idle 4 ชม. + service อื่นถือ connection บน defaultdb อยู่ 8 ตัว — ตอน deploy
# Render รัน instance ใหม่คู่กับตัวเก่า ทั้งคู่ขอ pool พร้อมกันจึงไม่มีทางพอ)
#
# ข้อแลกเปลี่ยนที่ต้องรู้: โปรเจกต์นี้ไม่ได้ตั้ง alembic ไว้ (มีใน requirements แต่ไม่มี
# ไฟล์ migration) บรรทัดนี้จึงเป็นกลไกสร้างตารางตัวจริง — ถ้ามันถูกข้าม ตารางใหม่จะยัง
# ไม่ถูกสร้างในรอบนั้น แต่ create_all เป็น idempotent และรันทุกครั้งที่บูต การบูตครั้ง
# ถัดไปที่ต่อ DB ได้จึงสร้างให้เองอยู่ดี · แลกกับการที่แอปยังบูตและเปิดพอร์ตไว้ได้
# (endpoint ที่ต้องใช้ DB พังเป็นราย request ตามปกติ และกู้เองเมื่อ connection ว่าง)
# ซึ่งดีกว่าปล่อยให้ทั้งบริการ deploy ไม่ขึ้นเลย
try:
    Base.metadata.create_all(bind=engine)
except Exception as _e:  # noqa: BLE001 — จงใจกว้าง: อะไรก็ตามที่นี่ต้องไม่ล้มการบูต
    import logging as _logging
    _logging.getLogger(__name__).error(
        "create_all() ล้มเหลว — แอปจะบูตต่อโดยไม่สร้าง/อัปเดตตาราง: %s", _e
    )

# ------------------------------
# App
# ------------------------------
app = FastAPI(
    title="NCAC API",
    version="1.1.2",
    contact={"name": "Narongkorn A. (Plug)"},
    license_info={"name": "MENA Transport Internal License"},
    description="NCAC API - Form + Approval Workflow"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# ------------------------------
# Custom Swagger UI
# ------------------------------
@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui():
    html = get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title="NCAC API Docs",
    )
    html.body += """
    <style>
        .swagger-ui .models { display: none !important; }
    </style>
    """
    return HTMLResponse(html.body)

# ------------------------------
# Import Routers
# ------------------------------
from routes import (
    auth,
    users,
    clients,
    departments,
    sites,
    locations,
    vehicles,
    driver_roles,
    masterdrivers,
    mastercauses,
    case_reports,
    positions,
    position_levels,
    accident_cases,
    provinces,
    districts,
    sub_districts,
    case_reports_investigate,complaint,complaint_master,fuel_routes,mixer_compensation,master_root_cause,
    accident_cases_investigate,
)

# 👉 Forms (แยกตาม topic)
from routes.forms.form_approval_routes import router as form_approval_router
from routes.forms.form_rule_routes import router as form_rule_router
from routes.forms.form_submission_routes import router as form_submission_router
from routes.forms.form_master_routes import router as form_master_router
from routes.allocation import allocation_routes
from routes.inspection import router as inspection_router
from routes.leave_booking.router import router as leave_booking_router
from routes.pipeline.pipeline_routes import router as pipeline_router
from routes.drivingdistance import router as drivingdistance_router
from routes.atms_maintenance import router as atms_maintenance_router
from routes.atms_tms import router as atms_tms_router
from routes.atms.openjob import router as atms_openjob_router
from routes.news_safety_talk import router as news_safety_talk_router
from routes.news_case_report import router as news_case_report_router
from routes.news_accident_case import router as news_accident_case_router
from routes.newyear_survey import router as newyear_survey_router


# ------------------------------
# Include Routers
# ------------------------------
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(clients.router)
app.include_router(departments.router)
app.include_router(sites.router)
app.include_router(locations.router)
app.include_router(vehicles.router)
app.include_router(driver_roles.router)
app.include_router(masterdrivers.router)
app.include_router(mastercauses.router)
app.include_router(case_reports.router)
app.include_router(positions.router)
app.include_router(position_levels.router)
app.include_router(accident_cases.router)
app.include_router(provinces.router)
app.include_router(districts.router)
app.include_router(sub_districts.router)
app.include_router(case_reports_investigate.router)
app.include_router(accident_cases_investigate.router)
app.include_router(complaint.router)
app.include_router(complaint_master.router)
app.include_router(allocation_routes.router)
app.include_router(fuel_routes.router)
app.include_router(mixer_compensation.router)
app.include_router(master_root_cause.router)
app.include_router(inspection_router)
app.include_router(news_safety_talk_router)
app.include_router(news_case_report_router)
app.include_router(news_accident_case_router)

# แบบสำรวจปีใหม่ (MongoDB: hr_service.newyear-survey)
app.include_router(newyear_survey_router)

#booking leave
app.include_router(leave_booking_router)

# Pipeline runner
app.include_router(pipeline_router)

# Analytics
app.include_router(drivingdistance_router)

# ATMS แจ้งซ่อม / ขอเปลี่ยนยาง
app.include_router(atms_maintenance_router)

# ATMS TMS — deliver order / ship to
app.include_router(atms_tms_router)

# ATMS (เปิด job แจ้งซ่อม / ขอเปลี่ยนยาง)
app.include_router(atms_openjob_router)

# ------------------------------
# 🚨 Forms Order (สำคัญ) 
# ------------------------------
# 1️⃣ Approval / Pending / Approve / Reject
app.include_router(form_approval_router)

# 2️⃣ Approval Rules CRUD
app.include_router(form_rule_router)

# 3️⃣ Submission (submit / get submission)
app.include_router(form_submission_router)

# 4️⃣ Form Master (Template)  ⚠️ มี /{form_code}
app.include_router(form_master_router)


# ------------------------------
# Startup
# ------------------------------
@app.on_event("startup")
async def startup_event():
    from routes.leave_booking.system_status import periodic_broadcast
    asyncio.create_task(periodic_broadcast())

    # Schedule pipelines at 02:00 Bangkok time (Asia/Bangkok = UTC+7)
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from pytz import timezone as tz
    from routes.pipeline.pipeline_routes import _run

    scheduler = AsyncIOScheduler(timezone=tz("Asia/Bangkok"))
    # Run LD → SCCO → CPAC sequentially so they don't hammer ATMS at the same time
    scheduler.add_job(_run, CronTrigger(hour=2, minute=0),  args=["ld"],   id="sched_ld")
    scheduler.add_job(_run, CronTrigger(hour=2, minute=20), args=["scco"], id="sched_scco")
    # deliver_result runs at 03:30 — after ATMS regenerates the monthly batch
    # files (~02:45-02:50) and clear of ld/scco
    scheduler.add_job(_run, CronTrigger(hour=3, minute=30), args=["deliver_result"], id="sched_deliver_result")
    # driver_cost runs at 05:45 — ATMS regenerates its batch files later (~05:00-05:05)
    scheduler.add_job(_run, CronTrigger(hour=5, minute=45), args=["driver_cost"], id="sched_driver_cost")
    # cpac is NOT scheduled here — it must run locally (launchd com.mena.mena-data-cpac
    # on the office Mac) because fleetlink/CPAC access is local-only. Manual trigger
    # via POST /pipeline/run/cpac remains available.
    # atms_procurement → mena-wms /pr · scheduler ทำงานเป็น UTC จริง (tz ไม่ apply)
    # ทุก 4 ชม. 06:00–22:00 น. ไทย → 06:00 full (2 เดือน), 10/14/18/22 light (7 วัน)
    # BKK→UTC (−7): 06→23(prev) · 10→03 · 14→07 · 18→11 · 22→15
    scheduler.add_job(_run, CronTrigger(hour=23, minute=0), args=["atms_procurement"], id="sched_atms_procurement")            # 06:00 BKK full
    scheduler.add_job(_run, CronTrigger(hour="3,7,11,15", minute=0), args=["atms_procurement_light"], id="sched_atms_procurement_light")  # 10/14/18/22 BKK light
    # master ซัพพลายเออร์ (เครดิตเทอมของ mena-wms /ap-tracking) — 06:40 BKK → 23:40 UTC
    # ต่อท้าย atms_procurement (23:00 UTC) เพื่อให้ ddCount นับจาก deposit_header ที่เพิ่งรีเฟรชแล้ว
    # และไม่ยิง ATMS พร้อมกัน · งานเบา ~11 คำขอ ใช้เวลาไม่ถึงนาที
    scheduler.add_job(_run, CronTrigger(hour=23, minute=40), args=["atms_supplier"], id="sched_atms_supplier")  # 06:40 BKK
    # engineon chain (BKK→UTC −7): GPS crunch 04:00 BKK → 21:00 UTC (prev day);
    # drivercost_ticket 06:10 BKK → 23:10 UTC (หลัง ATMS regen batch files ~05:00 BKK,
    # เหลื่อมจาก atms_procurement 23:00); trip summary 06:30 BKK → 23:30 UTC
    scheduler.add_job(_run, CronTrigger(hour=21, minute=0),  args=["engineon"], id="sched_engineon")                          # 04:00 BKK
    scheduler.add_job(_run, CronTrigger(hour=23, minute=10), args=["drivercost_ticket"], id="sched_drivercost_ticket")        # 06:10 BKK
    scheduler.add_job(_run, CronTrigger(hour=23, minute=30), args=["engineon_trip_summary"], id="sched_engineon_trip_summary")  # 06:30 BKK
    # maintenance (MR sync → maint_* + repair-analysis) 02:00 BKK → 19:00 UTC —
    # ATMS โหลดต่ำ และก่อน ld/scco (09:00/09:20 BKK จริงตาม UTC)
    scheduler.add_job(_run, CronTrigger(hour=19, minute=0), args=["maintenance"], id="sched_maintenance")  # 02:00 BKK
    # stockmovement → atms.stockmovement_v5 (mena-wms /deadstock /safety-stock /vendors,
    # dw_stockmovement ของ KPI-Motors, mena-intelligence /cost/*) — ย้ายมาจาก notebook
    # บนเครื่อง Mac ที่รันวันละครั้งและข้ามทั้งวันเมื่อเครื่องหลับ/เน็ตสะดุด
    # เวลาที่นี่เป็น UTC จริงเช่นเดียวกับ atms_procurement ด้านบน:
    #   full  ย้อน 5 เดือน  05:00 BKK → 22:00 UTC (ATMS ว่าง · ก่อน atms_procurement 23:00)
    #   light เดือนปัจจุบัน 08/12/16/20 BKK → 01/05/09/13 UTC — เยื้องจาก procurement light
    #   ที่จอง 03/07/11/15 UTC ไว้แล้ว เพราะยิงรายงาน ATMS พร้อมกันแล้ว ATMS จะ 500
    scheduler.add_job(_run, CronTrigger(hour=22, minute=0), args=["atms_stockmovement"], id="sched_atms_stockmovement")
    scheduler.add_job(_run, CronTrigger(hour="1,5,9,13", minute=0), args=["atms_stockmovement_light"], id="sched_atms_stockmovement_light")
    scheduler.start()
    import logging
    logging.getLogger(__name__).info("Pipeline scheduler started — LD 02:00, SCCO 02:20, deliver_result 03:30, driver_cost 05:45 (BKK); CPAC runs locally; engineon 04:00 / drivercost_ticket 06:10 / trip_summary 06:30 (BKK)")


# ------------------------------
# Root
# ------------------------------
@app.get("/")
def root():
    return {"message": "NCAC API is running 🚀"}
