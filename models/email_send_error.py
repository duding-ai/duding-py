from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from db import Base


class EmailSendError(Base):
    """Logged by services/email.py whenever Resend returns a non-2xx
    response, regardless of caller — outreach engine, CHKD, briefings,
    everything routes through send_email(), so logging here at the
    choke point covers every current and future sender automatically."""
    __tablename__ = "email_send_errors"

    id            = Column(Integer, primary_key=True, index=True)
    to_email      = Column(String, nullable=True)
    from_addr     = Column(String, nullable=True)
    subject       = Column(String, nullable=True)
    status_code   = Column(Integer, nullable=True)
    error_detail  = Column(Text, nullable=True)
    occurred_at   = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
