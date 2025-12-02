import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

# -------------------------------------------------------
# LOAD ENVIRONMENT VARIABLES
# -------------------------------------------------------
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
# APPLY DATABASE-LEVEL TIMEOUTS (PostgreSQL)
# -------------------------------------------------------
@event.listens_for(engine, "connect")
def set_postgres_timeout(dbapi_connection, connection_record):
    """Set safe timeouts for every new DB connection."""
    cursor = dbapi_connection.cursor()

    # ⏱️ Maximum duration for any query (e.g., 60 seconds)
    cursor.execute("SET statement_timeout = 60000;")

    # 💤 Maximum idle time in transaction before PostgreSQL kills the session (e.g., 60 seconds)
    cursor.execute("SET idle_in_transaction_session_timeout = 60000;")

    # 🧹 Optional: terminate session if backend becomes idle for too long (PostgreSQL ≥ 14)
    # cursor.execute("SET idle_session_timeout = 300000;")  # 5 minutes

    cursor.close()

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
    """
    Database session generator for FastAPI dependency.
    Ensures session is closed after request is processed.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
