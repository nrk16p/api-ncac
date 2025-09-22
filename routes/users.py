from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from database import get_db
from models import User

router = APIRouter(prefix="/users", tags=["Users"])

class UserBase(BaseModel):
    username: str
    employee_id: Optional[str] = None
    department_id: Optional[int] = None
    site_id: Optional[int] = None
    position_id: Optional[int] = None

class UserCreate(UserBase):
    password: str

class UserResponse(UserBase):
    id: int
    class Config:
        orm_mode = True

@router.get("/", response_model=List[UserResponse])
def get_users(db: Session = Depends(get_db)):
    return db.query(User).all()

@router.post("/", response_model=UserResponse, status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter_by(username=payload.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")
    user = User(
        username=payload.username,
        employee_id=payload.employee_id,
        department_id=payload.department_id,
        site_id=payload.site_id,
        position_id=payload.position_id
    )
    user.set_password(payload.password)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
