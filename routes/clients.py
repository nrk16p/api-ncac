from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from database import get_db
from models import Client

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
