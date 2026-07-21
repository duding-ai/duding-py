from sqlalchemy import Column, DateTime, Integer, String, text
from sqlalchemy.sql import func

from db import Base


class PlatformCredentials(Base):
    """Phase 2 scaffolding — OAuth tokens for official platform APIs
    (Meta Graph API for Instagram, TikTok Display API). Empty/absent
    rows mean 'not connected yet'; sync_platform_stats() no-ops until
    a row exists here with a live access_token."""
    __tablename__ = "platform_credentials"

    id            = Column(Integer, primary_key=True, index=True)
    client_id     = Column(Integer, nullable=False, index=True, server_default=text("1"))
    platform      = Column(String, nullable=False)   # tiktok | instagram
    access_token  = Column(String, nullable=True)
    refresh_token = Column(String, nullable=True)
    expires_at    = Column(DateTime(timezone=True), nullable=True)
    status        = Column(String, nullable=False, server_default=text("'disconnected'"))  # disconnected | connected | error
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    last_error    = Column(String, nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
