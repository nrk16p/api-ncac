from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from passlib.context import CryptContext
from database import Base

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


class PositionLevel(Base):
    __tablename__ = "position_levels"
    position_level_id = Column(Integer, primary_key=True, index=True)
    level_name = Column(String(50), unique=True, nullable=False)


class Position(Base):
    __tablename__ = "positions"
    position_id = Column(Integer, primary_key=True, index=True)
    position_name_th = Column(String(255), nullable=False)
    position_name_en = Column(String(255), nullable=False)
    position_level_id = Column(Integer, ForeignKey("position_levels.position_level_id"), nullable=False)

    level = relationship("PositionLevel", backref="positions")


class Department(Base):
    __tablename__ = "departments"
    department_id = Column(Integer, primary_key=True, index=True)
    department_name_th = Column(String(255), nullable=False)
    department_name_en = Column(String(255), nullable=False)


class Site(Base):
    __tablename__ = "sites"
    site_id = Column(Integer, primary_key=True, index=True)
    site_code = Column(String(50), unique=True)
    site_name_th = Column(String(255), nullable=False)
    site_name_en = Column(String(255), nullable=False)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    firstname = Column(String(100))
    lastname = Column(String(100))
    employee_id = Column(String(50))
    department_id = Column(Integer, ForeignKey("departments.department_id"))
    site_id = Column(Integer, ForeignKey("sites.site_id"))
    position_id = Column(Integer, ForeignKey("positions.position_id"))

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    department = relationship("Department", backref="users")
    site = relationship("Site", backref="users")
    position = relationship("Position", backref="users")

    def set_password(self, password: str):
        self.password_hash = pwd_context.hash(password)

    def check_password(self, password: str) -> bool:
        return pwd_context.verify(password, self.password_hash)
class Client(Base):
    __tablename__ = "clients"
    client_id = Column(Integer, primary_key=True, index=True)
    client_name = Column(String(255), nullable=False)
    contact_info = Column(String(255))
    site_id = Column(Integer)
