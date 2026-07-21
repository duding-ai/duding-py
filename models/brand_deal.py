from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from db import Base


class BrandDeal(Base):
    __tablename__ = "brand_deals"

    id          = Column(Integer, primary_key=True, index=True)
    prospect_id = Column(Integer, ForeignKey("brand_prospects.id"), nullable=False, index=True)
    deal_type   = Column(String, nullable=False)   # product_seeding | ugc_package | affiliate | paid_post | other
    value_usd   = Column(Float, nullable=True)
    status      = Column(String, nullable=False, server_default=text("'negotiating'"))
    terms       = Column(Text, nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    prospect = relationship("BrandProspect", back_populates="deals")
