from pydantic import BaseModel

class RootCauseBase(BaseModel):
    root_cause: str

class RootCauseCreate(RootCauseBase):
    pass

class RootCauseResponse(RootCauseBase):
    id: int

    class Config:
        orm_mode = True