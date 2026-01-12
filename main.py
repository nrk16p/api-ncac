from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.openapi.docs import get_swagger_ui_html

from database import engine, Base
import models

# Create tables (dev convenience)
Base.metadata.create_all(bind=engine)

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
    case_reports_investigate,
)

# 👉 Forms (แยกตาม topic)
from routes.forms.form_approval_routes import router as form_approval_router
from routes.forms.form_rule_routes import router as form_rule_router
from routes.forms.form_submission_routes import router as form_submission_router
from routes.forms.form_master_routes import router as form_master_router

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
# Root
# ------------------------------
@app.get("/")
def root():
    return {"message": "NCAC API is running 🚀"}
