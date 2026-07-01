from sqlalchemy import Column, Date, Integer, String, UniqueConstraint
from sqlalchemy.sql import func
from db import Base


class ChkdAiUsage(Base):
    __tablename__ = "chkd_ai_usage"
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_chkd_ai_usage_user_date"),)

    id            = Column(Integer, primary_key=True, index=True)
    user_id       = Column(String, nullable=False, index=True)
    date          = Column(Date, nullable=False, server_default=func.current_date())
    request_count = Column(Integer, nullable=False, default=0)
