from sqlalchemy import Column, Date, DateTime, Integer, Text
from sqlalchemy.sql import func

from db import Base


class SocialIntelligenceReport(Base):
    __tablename__ = "social_intelligence_reports"

    id        = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, nullable=False, index=True)   # clients.id
    week_of   = Column(Date, nullable=False)                  # Monday of the report week
    raw_data  = Column(Text, nullable=True)                   # JSON — Apify-shaped posts
    analysis  = Column(Text, nullable=True)                   # JSON — Claude output
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
