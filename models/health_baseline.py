from sqlalchemy import Column, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from db import Base


class HealthBaseline(Base):
    """Expected range per (agent, metric) — what 'normal' looks like,
    so the health monitor compares against reality instead of just
    flagging anything non-zero. Seeded on startup; editable in the DB
    if Tommy wants to tune a threshold without a code change."""
    __tablename__ = "health_baselines"
    __table_args__ = (UniqueConstraint("agent", "metric", name="uq_health_baseline_agent_metric"),)

    id           = Column(Integer, primary_key=True, index=True)
    agent        = Column(String, nullable=False)   # outreach | brand_deals | system
    metric       = Column(String, nullable=False)    # reply_rate_pct | bounce_rate_pct | held_for_review_pct | ...
    expected_min = Column(Float, nullable=True)
    expected_max = Column(Float, nullable=True)
    unit         = Column(String, nullable=False, default="pct")
    notes        = Column(Text, nullable=True)
    updated_at   = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
