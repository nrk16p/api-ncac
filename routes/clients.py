from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from database import get_db
from models import Client

router = APIRouter(prefix="/clients", tags=["Clients"])

class ClientCreate(BaseModel):
    client_name: str
    contact_info: Optional[str] = None

class ClientUpdate(BaseModel):
    client_name: Optional[str] = None
    contact_info: Optional[str] = None

class ClientResponse(BaseModel):
    client_id: int
    client_name: str
    contact_info: Optional[str] = None
    class Config:
        orm_mode = True

@router.post("/", response_model=ClientResponse, status_code=201)
def create_client(payload: ClientCreate, db: Session = Depends(get_db)):
    c = Client(client_name=payload.client_name, contact_info=payload.contact_info)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c

@router.get("/", response_model=List[ClientResponse])
def get_clients(db: Session = Depends(get_db)):
    return db.query(Client).all()

@router.put("/{client_id}")
def update_client(client_id: int, payload: ClientUpdate, db: Session = Depends(get_db)):
    c = db.query(Client).get(client_id)
    if not c:
        raise HTTPException(status_code=404, detail="Client not found")
    if payload.client_name is not None:
        c.client_name = payload.client_name
    if payload.contact_info is not None:
        c.contact_info = payload.contact_info
    db.commit()
    return {"message": "Client updated"}

@router.delete("/{client_id}")
def delete_client(client_id: int, db: Session = Depends(get_db)):
    c = db.query(Client).get(client_id)
    if not c:
        raise HTTPException(status_code=404, detail="Client not found")
    db.delete(c)
    db.commit()
    return {"message": "Client deleted"}
