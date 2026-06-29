from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from db import Base


class OutreachProspect(Base):
    __tablename__ = "outreach_prospects"

    id = Column(Integer, primary_key=True, index=True)
    source_input = Column(String, nullable=True)
    source_url = Column(String, nullable=True)
    business_name = Column(String, nullable=True)
    contact_name = Column(String, nullable=True)
    email = Column(String, nullable=False, index=True)
    website = Column(String, nullable=True)
    business_description = Column(Text, nullable=True)
    lever = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="outreach_pending")
    next_follow_up_at = Column(DateTime(timezone=True), nullable=True)
    last_contacted_at = Column(DateTime(timezone=True), nullable=True)
    follow_up_count = Column(Integer, nullable=False, default=0, server_default="0")
    last_email_subject = Column(String, nullable=True)
    last_message = Column(Text, nullable=True)
    email_quality = Column(String, nullable=True)   # 'direct' | 'generic'
    email_note = Column(String, nullable=True)       # shown in dashboard
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    activities = relationship(
        "OutreachActivity",
        back_populates="prospect",
        cascade="all, delete-orphan",
    )
