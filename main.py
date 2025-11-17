from fastapi import FastAPI
from database import engine, Base
import models

# Create tables (dev convenience; use Alembic migrations in prod)
Base.metadata.create_all(bind=engine)

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
    case_reports, positions, position_levels , accident_cases 
    , provinces, districts, sub_districts ,case_reports_investigate
)
app = FastAPI(
    title="NCAC API",
    version="1.1.0",
        contact={
        "name": "Narongkorn A. (Plug)"
    },
    license_info={
        "name": "MENA Transport Internal License"
    },
    description="""
### 🚀 NCAC API — Near-Miss & Accident Case Management System

This API powers the complete workflow for reporting, investigating, and resolving accident & near-miss cases across MENA Transport operations.

---

## 🔐 Authentication & User Management
- User login, registration (Argon2 hashing)
- Positions, departments, sites
- Reporter/driver linkage

---

## 📁 Master Data Modules
- Sites / Departments / Clients
- Driver roles / MasterDrivers
- Vehicles (head & tail)
- Locations: Province → District → Sub-district
- Causes (MasterCause)

---

## 📝 Case Report Module
- Create / Update / Delete cases
- Auto document numbering (NC-{SITE}-{YYMM}-{SEQ})
- Auto priority calculation:
  - Minor / Major / Crisis
- Product list (`products[]`)
- Attachments
- Filtering by site, driver, case status, priority, date range

---

## 🔍 Investigation Module
- 1:1 relationship per case
- Root cause analysis
- Claim type, insurance claim
- Product resellable status
- Cost allocation:
  - driver_cost / company_cost / remaining_damage_cost

---

## 🛠 Corrective Actions Module
- Stored under each investigation
- Multiple actions supported:
  - corrective_action  
  - pic_contract  
  - plan_date  
  - action_completed_date  

---

## 📄 Documentation Workflow (docs[] JSONB)
Stores accounting & compliance documents in flexible JSON array:

- Warning document  
- Debt document  
- Customer invoice  
- Insurance claim documents  
- Writeoff documents  
- Damage payment  
- Account attachment  

---

## 🚨 Accident Case Module (Standalone)
- Detailed case handling separate from case_reports
- Injury details, drug/alcohol tests
- Other party info
- Provincial location hierarchy

---



"""
)
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


@app.get("/")
def root():
    return {"message": "NCAC API is running 🚀"}
