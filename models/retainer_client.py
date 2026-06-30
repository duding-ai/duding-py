from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.sql import func

from db import Base


class RetainerClient(Base):
    __tablename__ = "retainer_clients"

    id = Column(Integer, primary_key=True, index=True)
    build_id = Column(String, index=True, nullable=False)  # Build.build_id (UUID)
    tier = Column(String, nullable=False)                   # 'growth' | 'scale'
    status = Column(String, nullable=False, default="pending_onboard")
    email = Column(String, nullable=False)
    contact_name = Column(String, nullable=True)
    business_name = Column(String, nullable=True)

    # Collected via onboarding form
    ad_account_email = Column(String, nullable=True)
    social_logins = Column(String, nullable=True)    # free-text, e.g. "@handle / page link"
    brand_assets_note = Column(String, nullable=True)

    accepted_at = Column(DateTime(timezone=True), nullable=True)
    onboarded_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
