import os, datetime, jwt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from dotenv import load_dotenv
from database import get_db
from models import User, Position, PositionLevel, Department, Site

load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY", "mysecretkey")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))

router = APIRouter(prefix="/auth", tags=["Auth"])

def create_access_token(identity: str) -> str:
    expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": identity, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

# ===== Schemas =====
class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    password: str
    firstname: str
    lastname: str
    employee_id: str | None = None
    department_id: int | None = None
    site_id: int | None = None
    position_id: int | None = None

# ===== Login =====
@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(username=payload.username).first()
    if user and user.check_password(payload.password):
        token = create_access_token(user.username)

        # Position & Level
        position_name = None
        position_level = None
        position_level_id = None
        if user.position_id:
            position = db.get(Position, user.position_id)
            if position:
                position_name = position.position_name_en
                level = db.get(PositionLevel, position.position_level_id)
                if level:
                    position_level = level.level_name
                    position_level_id = level.position_level_id

        # Department
        department_name = None
        if user.department_id:
            department = db.get(Department, user.department_id)
            if department:
                department_name = department.department_name_en

        # Site
        site_name = None
        if user.site_id:
            site = db.get(Site, user.site_id)
            if site:
                site_name = site.site_name_en

        return {
            "message": "Login success",
            "access_token": token,
            "user": {
                "id": user.id,
                "username": user.username,
                "firstname": user.firstname,
                "lastname": user.lastname,
                "employee_id": user.employee_id,
                "site": site_name,
                "department": department_name,
                "position": position_name,
                "position_level": position_level,
                "position_level_id": position_level_id,
            }
        }

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

# ===== Register =====
@router.post("/register", status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    # Check duplicate username
    if db.query(User).filter_by(username=payload.username).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists"
        )
    
    # Check duplicate employee_id
    if payload.employee_id and db.query(User).filter_by(employee_id=payload.employee_id).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Employee ID already exists"
        )

    try:
        user = User(
            username=payload.username,
            firstname=payload.firstname,
            lastname=payload.lastname,
            employee_id=payload.employee_id,
            department_id=payload.department_id,
            site_id=payload.site_id,
            position_id=payload.position_id,
        )
        user.set_password(payload.password)
        db.add(user)
        db.commit()
        db.refresh(user)

        token = create_access_token(user.username)

        return {
            "message": "User created successfully",
            "access_token": token,
            "user": {
                "id": user.id,
                "username": user.username,
                "firstname": user.firstname,
                "lastname": user.lastname,
                "employee_id": user.employee_id,
            }
        }

    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Database integrity error: Duplicate or invalid data"
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error: {str(e)}"
        )
