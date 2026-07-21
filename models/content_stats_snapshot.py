from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from db import Base


class ContentStatsSnapshot(Base):
    __tablename__ = "content_stats_snapshots"

    id                     = Column(Integer, primary_key=True, index=True)
    video_id               = Column(Integer, ForeignKey("content_videos.id"), nullable=False, index=True)
    captured_at            = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    hours_since_post       = Column(Float, nullable=True)   # computed at save time from video.posted_at

    views                  = Column(Integer, nullable=True)
    accounts_reached       = Column(Integer, nullable=True)   # IG only
    total_viewers          = Column(Integer, nullable=True)   # TikTok "Viewers" tab
    likes                  = Column(Integer, nullable=True)
    comments               = Column(Integer, nullable=True)
    shares                 = Column(Integer, nullable=True)
    reposts                = Column(Integer, nullable=True)
    saves                  = Column(Integer, nullable=True)
    profile_visits         = Column(Integer, nullable=True)
    bio_link_taps          = Column(Integer, nullable=True)
    follows                = Column(Integer, nullable=True)

    avg_watch_time_seconds = Column(Float, nullable=True)
    watched_full_pct       = Column(Float, nullable=True)   # TikTok
    retention_avg_pct      = Column(Float, nullable=True)   # "viewers watched X% of your video"
    skip_rate_pct          = Column(Float, nullable=True)   # IG "what impacts your views"
    drop_off_seconds       = Column(Float, nullable=True)   # "most viewers stopped at 0:0X"
    total_play_time_seconds = Column(Integer, nullable=True)

    traffic_sources        = Column(JSON, nullable=True)   # {"for_you": 78.2, "search": 2.6, ...}
    audience                = Column(JSON, nullable=True)   # {"male_pct":54, "age":{...}, "top_locations":{...}, ...}
    raw_extra               = Column(JSON, nullable=True)   # anything parsed that doesn't fit a named column

    video = relationship("ContentVideo", back_populates="snapshots")

    # ── Computed rates — not stored, always derived from this snapshot ──
    @staticmethod
    def _safe_div(a, b):
        if not a or not b:
            return None
        try:
            return a / b
        except ZeroDivisionError:
            return None

    @property
    def share_rate(self):
        return self._safe_div(self.shares, self.views)

    @property
    def save_rate(self):
        return self._safe_div(self.saves, self.views)

    @property
    def like_rate(self):
        return self._safe_div(self.likes, self.views)

    @property
    def comment_rate(self):
        return self._safe_div(self.comments, self.views)

    @property
    def profile_visit_rate(self):
        return self._safe_div(self.profile_visits, self.views)

    @property
    def bio_tap_conversion(self):
        return self._safe_div(self.bio_link_taps, self.profile_visits)

    @property
    def views_per_reach(self):
        return self._safe_div(self.views, self.accounts_reached)

    @property
    def completion_proxy(self):
        length = self.video.length_seconds if self.video else None
        return self._safe_div(self.avg_watch_time_seconds, length)
