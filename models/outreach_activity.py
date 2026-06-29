from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from db import Base


class OutreachActivity(Base):
    __tablename__ = "outreach_activities"

    id = Column(Integer, primary_key=True, index=True)
    prospect_id = Column(Integer, ForeignKey("outreach_prospects.id"), nullable=False)
    activity_type = Column(String, nullable=False)
    subject = Column(String, nullable=True)
    body_preview = Column(Text, nullable=True)
    status = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    prospect = relationship("OutreachProspect", back_populates="activities")
