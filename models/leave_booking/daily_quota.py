from sqlalchemy import Column, Integer, String, Date, BigInteger, UniqueConstraint, Index
from database import Base

class LeaveDailyQuota(Base):
    __tablename__ = "leave_daily_quota"

    daily_quota_id = Column(BigInteger, primary_key=True, index=True)

    fleet = Column(String(100), nullable=False, index=True)
    plant = Column(String(100), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)

    quota = Column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("fleet", "plant", "date", name="uq_daily_quota"),
        Index("idx_daily_lookup", "fleet", "plant", "date"),
    )   