from sqlalchemy import Column, DateTime, Integer, String, text
from sqlalchemy.sql import func

from db import Base


class Client(Base):
    __tablename__ = "clients"

    id            = Column(Integer, primary_key=True, index=True)
    name          = Column(String, nullable=False)
    type          = Column(String, nullable=False, server_default=text("'install'"))   # install | retainer | internal
    status        = Column(String, nullable=False, server_default=text("'active'"))    # active | paused | completed
    domain        = Column(String, nullable=True)         # client's product domain (e.g. getchkd.app)
    dashboard_url = Column(String, nullable=True)         # link to client's live product
    external_id   = Column(String, nullable=True, index=True)  # Build.build_id or RetainerClient.id
    notes         = Column(String, nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
