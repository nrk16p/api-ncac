from sqlalchemy import Column, String, Date, BigInteger, Text, Index
from database import Base

class LeaveBlackout(Base):
    __tablename__ = "leave_blackout_date"

    blackout_id = Column(BigInteger, primary_key=True, index=True)

    fleet = Column(String(100), nullable=False, index=True)
    plant = Column(String(100), nullable=True, index=True)  # NULL = all plants

    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)

    reason = Column(Text)

    __table_args__ = (
        Index("idx_blackout_lookup", "fleet", "plant", "start_date", "end_date"),
    )