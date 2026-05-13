"""Alembic environment — sync psycopg2 engine for migrations."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url

# Import Base so Alembic can autogenerate migrations from our models.
from hub.backend.models import Base  # noqa: F401  (registers all mapped classes)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Parse the asyncpg URL and reconstruct it for psycopg2 using URL.create so
# special characters in the password (e.g. "@") are handled correctly and don't
# corrupt the host field.
_raw = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://iothub:iothub@localhost:5432/iothub",
)
_parsed = make_url(_raw)
DATABASE_URL = URL.create(
    drivername="postgresql+psycopg2",
    username=_parsed.username,
    password=_parsed.password,
    host=_parsed.host,
    port=_parsed.port or 5432,
    database=_parsed.database,
    query={"sslmode": "disable"},
)


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
