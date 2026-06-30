import uuid
from sqlalchemy import Boolean, Column, DateTime, Integer, String, text
from sqlalchemy.sql import func

from db import Base


class Build(Base):
    __tablename__ = "builds"

    id = Column(Integer, primary_key=True, index=True)

    build_id = Column(
        String,
        unique=True,
        index=True,
        nullable=False,
        default=lambda: str(uuid.uuid4()),
    )

    contact_name = Column(String, nullable=True)
    email = Column(String, index=True, nullable=False)
    business_name = Column(String, nullable=True)

    business_type = Column(String, nullable=False)
    lead_volume_tier = Column(String, nullable=False)

    stripe_confirmed = Column(Boolean, nullable=False, server_default=text("false"))

    package_tier = Column(String, nullable=False)
    total_price_cents = Column(Integer, nullable=False)
    timeline_days = Column(Integer, nullable=False)

    deposit_amount_cents = Column(Integer, nullable=False, server_default=text("50000"))
    deposit_paid = Column(Boolean, nullable=False, server_default=text("false"))
    deposit_paid_at = Column(DateTime(timezone=True), nullable=True)
    deposit_payment_intent_id = Column(String, nullable=True)

    # CONFIGURED → DEPOSIT_PAID → INSTALLING → LIVE → CANCELED
    status = Column(String, nullable=False, server_default=text("'CONFIGURED'"))

    # Onboarding sequence flags
    onboarding_sent = Column(Boolean, nullable=False, server_default=text("false"))
    day3_sent = Column(Boolean, nullable=False, server_default=text("false"))
    day7_sent = Column(Boolean, nullable=False, server_default=text("false"))
    day10_sent = Column(Boolean, nullable=False, server_default=text("false"))

    # Post-completion flags
    completed_at = Column(DateTime(timezone=True), nullable=True)
    testimonial_sent = Column(Boolean, nullable=False, server_default=text("false"))
    upsell_sent = Column(Boolean, nullable=False, server_default=text("false"))
    upsell_day37_sent = Column(Boolean, nullable=False, server_default=text("false"))
    retainer_upsell_sent = Column(Boolean, nullable=False, server_default=text("false"))

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
