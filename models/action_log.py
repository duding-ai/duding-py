from sqlalchemy import Column, DateTime, Integer, String, Text, JSON
from sqlalchemy.sql import func

from db import Base


class ActionLog(Base):
    """Single audit trail for every self-healing action the system takes
    on its own — kill switches tripped, jobs auto-restarted, escalations
    filed. The point: a human catching up after time away reads this one
    table and knows exactly what happened, instead of reconstructing it
    from scattered print() logs."""
    __tablename__ = "action_log"

    id           = Column(Integer, primary_key=True, index=True)
    category     = Column(String, nullable=False)   # auto_pause | job_restart | escalation | self_healing_error
    severity     = Column(String, nullable=False, default="info")  # info | warning | critical
    agent        = Column(String, nullable=True)
    job_id       = Column(String, nullable=True)
    trigger      = Column(Text, nullable=False)
    action_taken = Column(Text, nullable=False)
    details      = Column(JSON, nullable=True)
    occurred_at  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
