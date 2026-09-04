from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from database import get_db
from models import Client, MasterDriver

router = APIRouter(prefix="/clients", tags=["Clients"])

# -------------------------
# 🔹 Schemas
# -------------------------
class ClientCreate(BaseModel):
    client_name: str
    contact_info: Optional[str] = None
    site_id: Optional[int] = None

class ClientUpdate(BaseModel):
    client_name: Optional[str] = None
    contact_info: Optional[str] = None
    site_id: Optional[int] = None

class ClientNameResponse(BaseModel):
    client_name: str

class ClientResponse(BaseModel):
    client_id: int
    client_name: str
    contact_info: Optional[str] = None
    site_id: Optional[int] = None

    class Config:
        orm_mode = True

# -------------------------
# 🔹 Create
# -------------------------
@router.post("/", response_model=ClientResponse, status_code=201)
def create_client(payload: ClientCreate, db: Session = Depends(get_db)):
    c = Client(
        client_name=payload.client_name,
        contact_info=payload.contact_info,
        site_id=payload.site_id
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c

# -------------------------
# 🔹 Read
# -------------------------
@router.get("/", response_model=List[ClientResponse])
def get_clients(db: Session = Depends(get_db)):
    return db.query(Client).all()

# -------------------------
# 🔹 Read — unique client_name จาก masterdrivers (3 เดือนย้อนหลัง)
# -------------------------
@router.get("/clients-unique", response_model=List[ClientNameResponse])
def get_driver_clients_unique(db: Session = Depends(get_db)):
    # month_year เก็บเป็น "MM-YYYY" — สร้างลิสต์เดือนปัจจุบัน + 2 เดือนก่อนหน้า
    today = datetime.today().replace(day=1)
    months = []
    for _ in range(3):
        months.append(today.strftime("%m-%Y"))
        today = (today - timedelta(days=1)).replace(day=1)

    rows = (
        db.query(MasterDriver.client_name)
        .filter(
            MasterDriver.month_year.in_(months),
            MasterDriver.client_name.isnot(None),
            MasterDriver.client_name != ""
        )
        .distinct()
        .order_by(MasterDriver.client_name)
        .all()
    )

    return [{"client_name": r[0]} for r in rows]

# -------------------------
# 🔹 Update
# -------------------------
@router.put("/{client_id}", response_model=ClientResponse)
def update_client(client_id: int, payload: ClientUpdate, db: Session = Depends(get_db)):
    c = db.query(Client).get(client_id)
    if not c:
        raise HTTPException(status_code=404, detail="Client not found")

    for field, value in payload.dict(exclude_unset=True).items():
        setattr(c, field, value)

    db.commit()
    db.refresh(c)
    return c

# -------------------------
# 🔹 Delete
# -------------------------
@router.delete("/{client_id}")
def delete_client(client_id: int, db: Session = Depends(get_db)):
    c = db.query(Client).get(client_id)
    if not c:
        raise HTTPException(status_code=404, detail="Client not found")
    db.delete(c)
    db.commit()
    return {"message": "Client deleted"}
