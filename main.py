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
)
app = FastAPI(title="NCAC API", version="1.0.0")

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

@app.get("/")
def root():
    return {"message": "NCAC API is running 🚀"}
