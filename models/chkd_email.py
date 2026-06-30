from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.sql import func

from db import Base


class ChkdEmail(Base):
    __tablename__ = "chkd_emails"

    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(String, nullable=False, index=True)
    email      = Column(String, nullable=False)
    email_type = Column(String, nullable=False)  # welcome | day3_reengagement | day7_streak
    sent_at    = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
