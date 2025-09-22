import os, datetime, jwt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from dotenv import load_dotenv
from database import get_db
from models import User, Position, PositionLevel

load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY", "mysecretkey")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))

router = APIRouter(prefix="/auth", tags=["Auth"])

def create_access_token(identity: str) -> str:
    expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": identity, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    password: str
    employee_id: str | None = None
    department_id: int | None = None
    site_id: int | None = None
    position_id: int | None = None

@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(username=payload.username).first()
    if user and user.check_password(payload.password):
        token = create_access_token(user.username)

        position_level = None
        if user.position_id:
            position = db.query(Position).get(user.position_id)
            if position:
                level = db.query(PositionLevel).get(position.position_level_id)
                position_level = level.level_name if level else None

        return {
            "message": "Login success",
            "access_token": token,
            "user": {
                "id": user.id,
                "username": user.username,
                "employee_id": user.employee_id,
                "site_id": user.site_id,
                "department_id": user.department_id,
                "position_id": user.position_id,
                "position_level": position_level
            }
        }
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

@router.post("/register", status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(User).filter_by(username=payload.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")
    user = User(
        username=payload.username,
        employee_id=payload.employee_id,
        department_id=payload.department_id,
        site_id=payload.site_id,
        position_id=payload.position_id,
    )
    user.set_password(payload.password)
    db.add(user)
    db.commit()
    token = create_access_token(user.username)
    return {"message": "User created successfully", "access_token": token}

@router.post("/forgot-password")
def forgot_password(username: str, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(username=username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": f"Password reset link sent for {username}"}
