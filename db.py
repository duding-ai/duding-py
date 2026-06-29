import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///duding.db")

# SQLite needs a directory to exist and a thread safety flag; PostgreSQL needs neither.
if DATABASE_URL.startswith("sqlite"):
    db_path = DATABASE_URL.replace("sqlite:////", "/").replace("sqlite:///", "")
    if db_path and not db_path.startswith(":"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    connect_args = {"check_same_thread": False}
else:
    connect_args = {}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()
