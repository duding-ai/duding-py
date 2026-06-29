# models/blueprint.py

from sqlalchemy import (
    Column,
    String,
    Integer,
    Boolean,
    DateTime,
    Text,
)
from sqlalchemy.sql import func
from sqlalchemy.dialects.sqlite import JSON

from db import Base


class Blueprint(Base):
    """
    Stores the result of a Duding 'Blueprint' diagnostic for a business.
    UUID is stored as a string in `id`.
    """

    __tablename__ = "blueprints"

    id = Column(String, primary_key=True)  # UUID stored as string
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    business_name = Column(String, nullable=False)
    industry = Column(String, nullable=True)

    leads_per_week = Column(Integer, nullable=True)
    follow_up_type = Column(String, nullable=True)
    workflow_rating = Column(Integer, nullable=True)  # 1–5
    brand_rating = Column(Integer, nullable=True)  # 1–5
    posts_last_week = Column(Integer, nullable=True)
    ads_running = Column(Boolean, nullable=True)

    # JSON blobs for scoring + summaries
    scores_json = Column(JSON, nullable=True)  # dict with 5 engine scores
    loss_estimation = Column(String, nullable=True)  # e.g. "$12,000 - $30,000"
    summary_json = Column(JSON, nullable=True)  # dict of issue summaries
    recommendation_text = Column(Text, nullable=True)

    pdf_path = Column(String, nullable=True)
