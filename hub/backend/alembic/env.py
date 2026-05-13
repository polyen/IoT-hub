"""Alembic environment — sync psycopg2 engine for migrations."""

from __future__ import annotations

import os
import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

# Import Base so Alembic can autogenerate migrations from our models.
from hub.backend.models import Base  # noqa: F401  (registers all mapped classes)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# App uses asyncpg; alembic uses psycopg2 (sync) to avoid asyncio thread-pool
# DNS failures that occur with Docker service names on some Linux configurations.
_async_url = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://iothub:iothub@localhost:5432/iothub",
)
DATABASE_URL = re.sub(
    r"postgresql\+asyncpg://",
    "postgresql+psycopg2://",
    _async_url,
    count=1,
).replace("?ssl=disable", "?sslmode=disable")


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: object) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)  # type: ignore[arg-type]
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(DATABASE_URL)
    with engine.connect() as connection:
        do_run_migrations(connection)
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
