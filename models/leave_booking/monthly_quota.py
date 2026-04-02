from sqlalchemy import Column, Integer, String, BigInteger, Boolean, DateTime, Numeric, UniqueConstraint
from sqlalchemy.sql import func
from database import Base

class MonthlyLeaveQuota(Base):
    __tablename__ = "monthly_leave_quota"

    quota_id = Column(BigInteger, primary_key=True, index=True)

    # fleet-level scope
    fleet = Column(String(100), nullable=False, index=True)
    year = Column(Integer, nullable=False, index=True)
    month = Column(Integer, nullable=False, index=True)

    total_driver = Column(Integer, nullable=False, default=0)

    # core (derived from percentage)
    percentage = Column(Numeric(5, 4), nullable=False)  # e.g. 0.03
    daily_quota = Column(Integer, nullable=False)
    monthly_quota_limit = Column(Integer, nullable=True)

    # driver constraint
    max_leave_per_driver = Column(Integer, nullable=False, default=4)

    # optional
    is_active = Column(Boolean, default=True)
    created_by = Column(String(50))
    updated_by = Column(String(50))
    remark = Column(String(255))

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # versioning
    version = Column(Integer, default=1)
    is_latest = Column(Boolean, default=True)

    __table_args__ = (
        UniqueConstraint("fleet", "year", "month", "version", name="uq_monthly_quota_version"),
    )   