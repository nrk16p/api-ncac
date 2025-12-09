from pydantic import BaseModel
from typing import Optional

class ClientBase(BaseModel):
    client_name: str
    contact_info: Optional[str] = None

class ClientCreate(ClientBase):
    pass

class ClientResponse(ClientBase):
    client_id: int
    site_id: int

    class Config:
        from_attributes = True
