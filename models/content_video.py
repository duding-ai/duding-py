from sqlalchemy import Column, DateTime, Float, Integer, JSON, String, Text, text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from db import Base


class ContentVideo(Base):
    __tablename__ = "content_videos"

    id                 = Column(Integer, primary_key=True, index=True)
    client_id          = Column(Integer, nullable=False, index=True, server_default=text("1"))  # clients.id
    platform           = Column(String, nullable=False)   # tiktok | instagram
    platform_video_id  = Column(String, nullable=True)
    series_number      = Column(Integer, nullable=True)
    title              = Column(String, nullable=False)
    posted_at          = Column(DateTime(timezone=True), nullable=False)
    length_seconds     = Column(Float, nullable=True)
    hook_text          = Column(Text, nullable=True)
    hook_style         = Column(String, nullable=True)   # confessional | cryptic | trend | question | data | other
    format_tags        = Column(JSON, nullable=True)      # e.g. ["trend_format","music_cut",...]
    caption_text       = Column(Text, nullable=True)
    cta_type           = Column(String, nullable=True)   # link_in_bio_spoken | link_in_bio_caption_only | share_prompt | comment_prompt | none
    sound_name         = Column(String, nullable=True)
    video_url          = Column(String, nullable=True)
    notes              = Column(Text, nullable=True)
    created_at         = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    snapshots = relationship(
        "ContentStatsSnapshot",
        back_populates="video",
        cascade="all, delete-orphan",
        order_by="ContentStatsSnapshot.captured_at",
    )

    @property
    def latest_snapshot(self):
        return self.snapshots[-1] if self.snapshots else None
