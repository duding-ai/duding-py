from sqlalchemy import Column, DateTime, Integer, JSON, String
from sqlalchemy.sql import func

from db import Base


class HealthCheckRun(Base):
    """One row per system_health_monitor run. flags is a JSON array of
    {severity, agent, metric, message, action, value, expected}."""
    __tablename__ = "health_check_runs"

    id             = Column(Integer, primary_key=True, index=True)
    run_at         = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    overall_status = Column(String, nullable=False)   # healthy | warning | critical
    flags          = Column(JSON, nullable=False, default=list)
    raw_metrics    = Column(JSON, nullable=True)
    created_at     = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
