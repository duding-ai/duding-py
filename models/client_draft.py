from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from db import Base


class ClientDraft(Base):
    __tablename__ = "client_drafts"

    id = Column(Integer, primary_key=True, index=True)
    prospect_id = Column(Integer, ForeignKey("outreach_prospects.id"), nullable=True)
    draft_type = Column(String, nullable=False)  # reply_response | followup
    to_email = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    approved = Column(Boolean, nullable=False, server_default="0")
    sent = Column(Boolean, nullable=False, server_default="0")
    sent_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
