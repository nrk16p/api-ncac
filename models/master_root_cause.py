from sqlalchemy import Column, Integer, String, Index
from database import Base

class MasterRootCause(Base):
    __tablename__ = "master_root_cause"

    id = Column(Integer, primary_key=True, index=True)
    root_cause = Column(String, nullable=False)

    __table_args__ = (
        Index("idx_root_cause_unique", "root_cause", unique=True),
    )