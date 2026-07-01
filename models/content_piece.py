from sqlalchemy import Column, Date, DateTime, Integer, String, Text, text
from sqlalchemy.sql import func

from db import Base


class ContentPiece(Base):
    __tablename__ = "content_pieces"

    id           = Column(Integer, primary_key=True, index=True)
    client_id    = Column(Integer, nullable=False, index=True)    # clients.id
    week_of      = Column(Date, nullable=False)
    content_type = Column(String, nullable=False)
    # quote_card | stat_card | feature_carousel | educational_carousel | power_words_card

    title        = Column(String, nullable=True)                  # internal label
    caption      = Column(Text, nullable=True)                    # Instagram caption
    hashtags     = Column(Text, nullable=True)                    # JSON array of strings
    image_data   = Column(Text, nullable=True)                    # JSON array of base64 PNGs
    slide_count  = Column(Integer, nullable=False, server_default=text("1"))

    status       = Column(String, nullable=False, server_default=text("'draft'"))
    # draft | approved | posted | render_failed

    created_at   = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
