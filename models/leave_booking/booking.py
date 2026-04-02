from sqlalchemy import Column, String, Date, BigInteger, Text, DateTime, Index
from sqlalchemy.sql import func
from database import Base

class DriverLeaveBooking(Base):
    __tablename__ = "driver_leave_booking"

    booking_id = Column(BigInteger, primary_key=True, index=True)

    driver_id = Column(String(50), nullable=False, index=True)
    fleet = Column(String(100), nullable=False, index=True)
    plant = Column(String(100), nullable=False, index=True)

    leave_date = Column(Date, nullable=False, index=True)

    leave_type = Column(String(50))
    remark = Column(Text)

    status = Column(String(20), default="pending")  # pending/approve/reject

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_leave_date_status", "leave_date", "status"),
        Index("idx_driver_month", "driver_id", "leave_date"),
    )