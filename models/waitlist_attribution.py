from sqlalchemy import Column, Date, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from db import Base


class WaitlistAttribution(Base):
    """One row per (date, UTM source) — nightly rollup of CHKD waitlist
    signups pulled from Supabase, used to overlay against content post
    dates on the Insights page."""
    __tablename__ = "waitlist_attribution"
    __table_args__ = (UniqueConstraint("date", "source", name="uq_waitlist_attribution_date_source"),)

    id         = Column(Integer, primary_key=True, index=True)
    date       = Column(Date, nullable=False, index=True)
    source     = Column(String, nullable=False)   # tiktok | instagram | direct | ...
    signups    = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
