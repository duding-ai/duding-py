from sqlalchemy import Column, DateTime, Integer, Text
from sqlalchemy.sql import func

from db import Base


class OutreachTestBatch(Base):
    """Singleton row (id=1) — a bounded, self-terminating outreach trial,
    independent of OUTREACH_SENDING_ENABLED and its warmup ramp (per
    Tommy's 2026-07-28 request: run a controlled 15-20 contact test
    without touching the full ramp or the Aug 4 re-enable gate).

    Active when target is set and completed_at is NULL. See
    services/outreach_test_batch.py for the pacing/completion logic and
    outreach_engine.job_send_next_queued for how it's wired into the
    actual send path — job_process_followups is untouched and stays
    off for the whole run, since this table only ever gates initial
    sends."""
    __tablename__ = "outreach_test_batch"

    id             = Column(Integer, primary_key=True)
    target         = Column(Integer, nullable=True)
    started_at     = Column(DateTime(timezone=True), nullable=True)
    sent_count     = Column(Integer, nullable=False, default=0)
    last_sent_at   = Column(DateTime(timezone=True), nullable=True)
    completed_at   = Column(DateTime(timezone=True), nullable=True)
    stopped_reason = Column(Text, nullable=True)  # target_reached | circuit_breaker | manual_stop
    updated_at     = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
