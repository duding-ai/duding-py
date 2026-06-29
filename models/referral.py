from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.sql import func

from db import Base


class Referral(Base):
    __tablename__ = "referrals"

    id = Column(Integer, primary_key=True, index=True)
    referrer_build_id = Column(Integer, ForeignKey("builds.id"), nullable=False)
    referrer_email = Column(String, nullable=False)
    referrer_business = Column(String, nullable=True)
    referred_email = Column(String, nullable=True)
    referred_business = Column(String, nullable=True)
    deposit_paid = Column(Boolean, nullable=False, server_default="0")
    credit_issued = Column(Boolean, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
