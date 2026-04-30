import os
from datetime import datetime, timedelta, timezone
import jwt

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from dotenv import load_dotenv
from sqlalchemy import or_

# 🔑 GOOGLE AUTH (สำคัญมาก)
from google.oauth2 import id_token
from google.auth.transport.requests import Request as GoogleRequest

from database import get_db
from models import User, Position, PositionLevel, Department, Site

# ============================================================
# ENV
# ============================================================
load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "mysecretkey")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
ALLOWED_GOOGLE_DOMAIN = "menatransport.co.th"

router = APIRouter(prefix="/auth", tags=["Auth"])

print("✅ GOOGLE_CLIENT_ID =", GOOGLE_CLIENT_ID)

# ============================================================
# JWT
# ============================================================
def create_access_token(subject: str):
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=ACCESS_TOKEN_EXPIRE_MINUTES
    )

    to_encode = {
        "sub": subject,
        "exp": expire
    }

    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt
# ============================================================
# Schemas
# ============================================================
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
    email:str | None = None
    employee_status :str | None = None


class GoogleLoginRequest(BaseModel):
    id_token: str

# ============================================================
# Helper: Verify Google Token (FIXED)
# ============================================================
def verify_google_token(token: str):
    try:
        idinfo = id_token.verify_oauth2_token(
            token,
            GoogleRequest(),          # ✅ ตัวนี้เท่านั้น
            GOOGLE_CLIENT_ID
        )

        # 🔐 ตรวจ email
        email = idinfo.get("email")
        if not email:
            raise HTTPException(status_code=400, detail="No email in Google token")

        # 🔐 ตรวจ domain
        domain = email.split("@")[-1].lower()
        if domain != ALLOWED_GOOGLE_DOMAIN:
            raise HTTPException(
                status_code=403,
                detail="Google account domain is not allowed"
            )

        return idinfo

    except HTTPException:
        raise
    except Exception as e:
        print("❌ GOOGLE VERIFY ERROR =", e)
        raise HTTPException(
            status_code=401,
            detail="Invalid Google token"
        )

# ============================================================
# Helper: Build user response (reuse logic เดิม)
# ============================================================
def build_user_response(user: User, db: Session):
    # Position
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
        "id": user.id,
        "username": user.username,
        "firstname": user.firstname,
        "lastname": user.lastname,
        "employee_id": user.employee_id,
        "site": site_name,
        "site_id": user.site_id,
        "department": department_name,
        "department_id":user.department_id,
        "position": position_name,
        "position_level": position_level,
        "position_level_id": position_level_id,
        "image_url": user.image_url or None,
        "last_login": user.last_login,    # (optional แต่แนะนำ)
        "employee_status": user.employee_status,    # (optional แต่แนะนำ)
     
    }


# ============================================================
# LOGIN (LOCAL)
# ============================================================
@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(username=payload.username).first()

    # 🔒 Google user ห้าม login ด้วย password
    # if user and user.password_hash == "__GOOGLE__":
    #     raise HTTPException(
    #         status_code=status.HTTP_401_UNAUTHORIZED,
    #         detail="Use Google login"
    #     )

    if user and user.check_password(payload.password):
        token = create_access_token(user.username)

        return {
            "message": "Login success",
            "access_token": token,
            "user": build_user_response(user, db)
        }

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials"
    )

# ============================================================
# REGISTER (LOCAL) 5
# ============================================================
@router.post("/register", status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(User).filter_by(username=payload.username).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists"
        )

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
            email=payload.email,
            employee_status=payload.employee_status,

        )
        user.set_password(payload.password)

        db.add(user)
        db.commit()
        db.refresh(user)

        token = create_access_token(user.username)

        return {
            "message": "User created successfully",
            "access_token": token,
            "user": build_user_response(user, db)
        }

    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Database integrity error"
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

# ============================================================
# LOGIN (GOOGLE)
# ============================================================
@router.post("/login/google")
def login_google(payload: GoogleLoginRequest, db: Session = Depends(get_db)):
    idinfo = verify_google_token(payload.id_token)

    email = idinfo["email"]
    username = email.split("@")[0]
    employee_id = username
    image_url = idinfo.get("picture")

    user = db.query(User).filter(
        or_(
            User.username == username,
            User.employee_id == employee_id,
            User.email == email
        )
    ).first()

    now = datetime.now(timezone.utc)   # ✅ จุดเดียวใช้ทั้ง function

    if not user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ไม่พบบัญชีพนักงานในระบบ กรุณาติดต่อไอทีเพื่อสร้างบัญชีให้ก่อน"
        )
        # user = User(
        #     username=username,
        #     email=email,
        #     firstname=idinfo.get("given_name"),
        #     lastname=idinfo.get("family_name"),
        #     employee_id=employee_id,
        #     image_url=image_url,
        #     last_login=now
        # )
        # user.password_hash = "__GOOGLE__"
        # db.add(user)
        # db.commit()
        # db.refresh(user)
    else:
        user.last_login = now

        if image_url:
            user.image_url = image_url

        if user.email and user.username == user.email:
            user.username = username
            user.employee_id = username

        db.commit()

    token = create_access_token(user.username)

    return {
        "message": "Login with Google success",
        "access_token": token,
        "user": build_user_response(user, db)
    }
