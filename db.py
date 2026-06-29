import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Local default: sqlite:///duding.db  (relative, dev)
# Railway:       set DATABASE_URL=sqlite:////data/duding.db  (volume mount at /data)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///duding.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()
