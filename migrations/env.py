from __future__ import annotations

import sys
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from sqlalchemy import create_engine

from alembic import context

# -------------------------------------------------------------------
# ADD PROJECT ROOT TO PYTHON PATH
# -------------------------------------------------------------------
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(BASE_DIR)

# -------------------------------------------------------------------
# IMPORT YOUR DB + MODELS
# -------------------------------------------------------------------
from db import Base  # Base = declarative_base()
from models.lead import Lead
from models.lead_event import LeadEvent

# -------------------------------------------------------------------
# Alembic Config
# -------------------------------------------------------------------
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for Alembic autogeneration
target_metadata = Base.metadata


# -------------------------------------------------------------------
# DATABASE URL
# -------------------------------------------------------------------
def get_url():
    import os
    return os.getenv("DATABASE_URL", "sqlite:///duding.db")


# -------------------------------------------------------------------
# RUN MIGRATIONS OFFLINE
# -------------------------------------------------------------------
def run_migrations_offline():
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


# -------------------------------------------------------------------
# RUN MIGRATIONS ONLINE
# -------------------------------------------------------------------
def run_migrations_online():
    connectable = create_engine(get_url())

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


# -------------------------------------------------------------------
# ENTRYPOINT
# -------------------------------------------------------------------
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
