from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from db import Base


class JobHealth(Base):
    """Last-known execution state per APScheduler job id. Written by a
    scheduler-wide listener (see outreach_engine.py::_job_listener),
    not by individual job functions — so every job added to the
    scheduler, present or future, is tracked automatically."""
    __tablename__ = "job_health"

    job_id            = Column(String, primary_key=True)
    last_success_at   = Column(DateTime(timezone=True), nullable=True)
    last_error_at     = Column(DateTime(timezone=True), nullable=True)
    last_error_message = Column(Text, nullable=True)
    consecutive_errors = Column(Integer, nullable=False, default=0)
    updated_at        = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
