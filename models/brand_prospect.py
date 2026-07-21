from sqlalchemy import Column, DateTime, Integer, String, Text, text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from db import Base


class BrandProspect(Base):
    __tablename__ = "brand_prospects"

    id               = Column(Integer, primary_key=True, index=True)
    client_id        = Column(Integer, nullable=False, index=True, server_default=text("1"))  # clients.id

    brand_name       = Column(String, nullable=False)
    website          = Column(String, nullable=True)
    industry         = Column(String, nullable=True)   # supplements | fitness_apparel | grooming | faith | productivity | food | other

    contact_email    = Column(String, nullable=True, index=True)
    contact_name     = Column(String, nullable=True)
    email_type       = Column(String, nullable=True)   # direct | generic

    source           = Column(String, nullable=True)   # seed_list | search_discovery | competitor_mention | manual
    instagram_handle = Column(String, nullable=True)
    tiktok_handle    = Column(String, nullable=True)

    status = Column(String, nullable=False, server_default=text("'new'"))
    # new | verified | queued | sent | held_for_review | replied | negotiating |
    # deal_won | deal_lost | bounced | unsubscribed | rejected (manually declined from held_for_review)

    found_at          = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_contacted_at = Column(DateTime(timezone=True), nullable=True)
    notes             = Column(Text, nullable=True)

    emails = relationship(
        "BrandOutreachEmail",
        back_populates="prospect",
        cascade="all, delete-orphan",
        order_by="BrandOutreachEmail.sent_at",
    )
    deals = relationship(
        "BrandDeal",
        back_populates="prospect",
        cascade="all, delete-orphan",
    )
