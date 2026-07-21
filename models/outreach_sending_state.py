from sqlalchemy import Column, DateTime, Integer
from sqlalchemy.sql import func

from db import Base


class OutreachSendingState(Base):
    """Singleton row (id=1) tracking when OUTREACH_SENDING_ENABLED was
    last observed flipping false->true, so the warmup ramp (10/day,
    +5/week, cap 50) can be computed from real elapsed time rather than
    process uptime. enabled_since is cleared back to NULL the moment
    the env var is observed false again, so the ramp always restarts
    from 10/day on the next re-enable — a stop-and-restart is treated
    as a fresh warmup, matching how sending-domain reputation actually
    works."""
    __tablename__ = "outreach_sending_state"

    id             = Column(Integer, primary_key=True)
    enabled_since  = Column(DateTime(timezone=True), nullable=True)
    updated_at     = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
