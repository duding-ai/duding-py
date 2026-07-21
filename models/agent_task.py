from sqlalchemy import Column, Date, DateTime, JSON, String, Text, Integer
from sqlalchemy.sql import func

from db import Base


class AgentTask(Base):
    """Durable checklist/task queue for work that needs a human
    decision at a specific gate — not a scheduler job, not a dashboard
    action, a persisted record of 'this needs Tommy's judgment on or
    after this date, here's what has to be true first.'"""
    __tablename__ = "agent_tasks"

    id         = Column(Integer, primary_key=True, index=True)
    title      = Column(String, nullable=False)
    status     = Column(String, nullable=False, default="pending")  # pending | human_gated | done | cancelled
    due_date   = Column(Date, nullable=True)
    criteria   = Column(JSON, nullable=True)   # list[{"description": str, "met": bool|None, "detail": str}]
    notes      = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
