from sqlalchemy import Column, DateTime, Integer, Text
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
    works.

    auto_paused_at / auto_pause_reason are the self-healing kill switch
    (services/self_healing.py): the app can't flip the Railway env var
    on itself, so this is a DB-backed shadow gate ANDed with
    OUTREACH_SENDING_ENABLED in outreach_engine._outreach_sending_enabled().
    Only a human clears it (POST /dashboard/outreach/engine/clear-auto-pause)
    — the sweep that trips it never clears it itself."""
    __tablename__ = "outreach_sending_state"

    id                 = Column(Integer, primary_key=True)
    enabled_since      = Column(DateTime(timezone=True), nullable=True)
    auto_paused_at     = Column(DateTime(timezone=True), nullable=True)
    auto_pause_reason  = Column(Text, nullable=True)
    updated_at         = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
