import os
from fastapi import HTTPException
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
#
# งบ connection ของคลัสเตอร์ DigitalOcean นี้คือ max_connections = 25 ทั้งก้อน
# และมีคนแบ่งกันใช้เยอะกว่าที่คิด (วัดจริง 04/09/2026): ระบบของ DO เอง ~8
# (pghoard / pg_cron / management-agent / failover) + service อื่นบน Render ที่
# ถือ connection ค้างบน defaultdb อีก ~8 + client อย่าง DBeaver ที่เปิดค้างทั้งวัน
# เหลือถึงเราจริง ๆ ไม่ถึง 10
#
# ที่สำคัญกว่านั้น: ตอน deploy ทุกครั้ง Render รัน instance ใหม่ **คู่ขนาน** กับ
# ตัวเก่า ค่า pool จึงต้องคูณสอง ของเดิม 5+5 กับ 2+3 = 15 ต่อ instance → 30 ตอน
# deploy ซึ่งเกินทั้งคลัสเตอร์ ทำให้ deploy ล้มซ้ำ ๆ (เห็นเป็น "No open ports
# detected" เพราะ create_all() ใน main.py ตายก่อนเปิดพอร์ต)
#
# ค่าปัจจุบัน 2+3 = 5 ต่อ instance → 10 ตอน deploy ซึ่งอยู่ในงบ
# uvicorn ตัวนี้รัน worker เดียวบน 0.5 CPU งานพร้อมกัน 5 ก้อนจึงเหลือเฟือ
# (pool_timeout=10 ทำให้ request ที่มาเกินรอไม่เกิน 10 วิ แทนที่จะค้างยาว)
#
# ปรับต่อได้ด้วย env var โดยไม่ต้องแก้โค้ด:
#   DB_POOL_SIZE     (default 2)
#   DB_MAX_OVERFLOW  (default 3)
# -------------------------------------------------------
_pool_size     = int(os.getenv("DB_POOL_SIZE", "2"))
_max_overflow  = int(os.getenv("DB_MAX_OVERFLOW", "3"))

engine = create_engine(
    DATABASE_URL,
    pool_size=_pool_size,
    max_overflow=_max_overflow,
    pool_timeout=10,         # fail fast — don't let requests queue for 30s
    pool_recycle=1800,       # recycle connection every 30 min (prevent stale connections)
    pool_pre_ping=True,      # check if connection is alive before using it
    echo=False,
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
# DATALAKE ENGINE (analytics tables)
#
# drivingdistance lives in the `datalake` database on the same
# DigitalOcean cluster, written by the GPS ETLs (terminus / dtc /
# thaitracking). Reading it in place keeps one copy of the data instead
# of mirroring it into ncacdb.
#
# Pool is deliberately small — the cluster's connection budget is shared
# with ncacdb and the pipeline subprocesses (ดูคำอธิบายงบ 25 connection ด้านบน;
# ลดจาก 2+3 เหลือ 1+1 ด้วยเหตุผลเดียวกัน — /drivingdistance ถูกเรียกไม่บ่อย)
#   DATALAKE_URL            (required for /drivingdistance)
#   DATALAKE_POOL_SIZE      (default 1)
#   DATALAKE_MAX_OVERFLOW   (default 1)
# -------------------------------------------------------
DATALAKE_URL = os.getenv("DATALAKE_URL")

datalake_engine = None
DatalakeSessionLocal = None

if DATALAKE_URL:
    datalake_engine = create_engine(
        DATALAKE_URL,
        pool_size=int(os.getenv("DATALAKE_POOL_SIZE", "1")),
        max_overflow=int(os.getenv("DATALAKE_MAX_OVERFLOW", "1")),
        pool_timeout=10,
        pool_recycle=1800,
        pool_pre_ping=True,
        echo=False,
        future=True,
    )
    event.listen(datalake_engine, "connect", set_postgres_timeout)
    DatalakeSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=datalake_engine,
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

# Separate metadata for tables that live in `datalake`, so that
# Base.metadata.create_all(bind=engine) in main.py does not create empty
# shadow copies of them inside ncacdb.
DatalakeBase = declarative_base()

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


def get_datalake_db():
    """Session for the `datalake` database (drivingdistance)."""
    if DatalakeSessionLocal is None:
        raise HTTPException(
            status_code=503,
            detail="DATALAKE_URL is not configured on this server",
        )
    db = DatalakeSessionLocal()
    try:
        yield db
    finally:
        db.close()
