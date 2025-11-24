import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

# -------------------------------------------------------
# DATABASE CONFIGURATION
# -------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("❌ DATABASE_URL not found in environment variables")

# -------------------------------------------------------
# POSTGRESQL ENGINE WITH CONNECTION POOL
# -------------------------------------------------------
engine = create_engine(
    DATABASE_URL,
    pool_size=10,            # default is 5 — increase for concurrent API calls
    max_overflow=20,         # how many extra temporary connections can be opened
    pool_timeout=30,         # seconds to wait before raising error if pool is full
    pool_recycle=1800,       # recycle connection every 30 min (prevent stale connections)
    pool_pre_ping=True,      # check if connection is alive before using it
    echo=False,              # set to True for SQL debug logging
    future=True,
)

# -------------------------------------------------------
# SESSION FACTORY
# -------------------------------------------------------
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

# -------------------------------------------------------
# BASE CLASS FOR MODELS
# -------------------------------------------------------
Base = declarative_base()

# -------------------------------------------------------
# FASTAPI DEPENDENCY
# -------------------------------------------------------
def get_db():
    """Database session generator for FastAPI dependency"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
