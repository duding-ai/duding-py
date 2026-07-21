from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from db import Base


class BrandOutreachEmail(Base):
    __tablename__ = "brand_outreach_emails"

    id                = Column(Integer, primary_key=True, index=True)
    prospect_id       = Column(Integer, ForeignKey("brand_prospects.id"), nullable=False, index=True)
    sent_at           = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    subject           = Column(String, nullable=True)
    body              = Column(Text, nullable=True)
    sequence_step     = Column(Integer, nullable=False, default=1)   # 1 = initial, 2 = follow-up
    resend_message_id = Column(String, nullable=True)

    prospect = relationship("BrandProspect", back_populates="emails")
