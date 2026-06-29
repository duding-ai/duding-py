from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from db import Base


class ContentIdea(Base):
    __tablename__ = "content_ideas"

    id = Column(Integer, primary_key=True, index=True)
    idea_type = Column(String, nullable=False)  # instagram_caption | video_hook | content_angle
    content = Column(Text, nullable=False)
    source = Column(String, nullable=True)  # business name, search term, or "weekly_stats"
    created_at = Column(DateTime(timezone=True), server_default=func.now())
