import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Local default: sqlite:///duding.db  (relative, dev)
# Railway:       set DATABASE_URL=sqlite:////data/duding.db  (volume mount at /data)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///duding.db")

# Ensure the directory exists for SQLite file paths (e.g. /data on Railway)
if DATABASE_URL.startswith("sqlite:///"):
    db_path = DATABASE_URL.replace("sqlite:////", "/").replace("sqlite:///", "")
    if db_path and not db_path.startswith(":"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()
