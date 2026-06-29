# models/business_settings.py

from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
)
from sqlalchemy.sql import func

from db import Base


class BusinessSettings(Base):
    __tablename__ = "business_settings"

    id = Column(Integer, primary_key=True)
    company_name = Column(String, nullable=False)
    industry = Column(String, nullable=True)
    logo_url = Column(String, nullable=True)
    website_url = Column(String, nullable=True)
    email_from_name = Column(String, nullable=True)
    email_from_address = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
