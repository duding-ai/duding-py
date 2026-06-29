from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship

from db import Base


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(String, nullable=False)  # stored as ISO text
    name = Column(String, nullable=True)
    email = Column(String, nullable=True, index=True)
    business = Column(String, nullable=True)
    budget = Column(String, nullable=True)
    link = Column(String, nullable=True)
    role = Column(String, nullable=True)
    cadence = Column(String, nullable=True)
    recent = Column(String, nullable=True)
    status = Column(String, nullable=True)
    run_count = Column(Integer, nullable=True, default=0)
    last_run = Column(String, nullable=True)

    # Relationship to LeadEvent
    events = relationship("LeadEvent", back_populates="lead")
