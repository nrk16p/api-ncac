from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload

from pydantic import BaseModel
from typing import List, Optional
from database import get_db
from models import User , Department, Position,Site

router = APIRouter(prefix="/users", tags=["Users"])
from sqlalchemy.orm import Session
from models import User, PositionLevel

def build_user_response(user: User, db: Session):
    # Position
    position_name = None
    position_level = None

    if user.position:
        position_name = user.position.position_name_en

        if user.position.level:
            position_level = user.position.level.level_name

    # Department
    department_name = None
    if user.department:
        department_name = user.department.department_name_en

    # Site
    site_name = None
    if user.site:
        site_name = user.site.site_name_en

    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "employee_id": user.employee_id,
        "employee_status": user.employee_status,
        "firstname": user.firstname,
        "lastname": user.lastname,
        "department": department_name,
        "site": site_name,
        "position": position_name,
        "position_level": position_level,
    }

class UserBase(BaseModel):
    username: str
    employee_id: Optional[str] = None
    department_id: Optional[int] = None
    site_id: Optional[int] = None
    position_id: Optional[int] = None
    email : Optional[str]
    employee_status : Optional[str]
    firstname : Optional[str]
    lastname : Optional[str]

class UserCreate(UserBase):
    password: str

class UserResponse(BaseModel):
    id: int
    username: str
    email: Optional[str]
    employee_id: Optional[str]
    employee_status: Optional[str]
    firstname: Optional[str]
    lastname: Optional[str]

    department: Optional[str]
    site: Optional[str]
    position: Optional[str]
    position_level: Optional[str]

    class Config:
        orm_mode = True



@router.get("/", response_model=List[UserResponse])
def get_users(db: Session = Depends(get_db)):
    users = (
        db.query(User)
        .options(
            joinedload(User.department),
            joinedload(User.site),
            joinedload(User.position)
                .joinedload(Position.level)
        )
        .all()
    )

    return [build_user_response(u, db) for u in users]

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
