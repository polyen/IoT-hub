"""Alembic environment — async SQLAlchemy with asyncpg."""

from __future__ import annotations

import asyncio
import os
import re
import socket
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

# Import Base so Alembic can autogenerate migrations from our models.
from hub.backend.models import Base  # noqa: F401  (registers all mapped classes)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://iothub:iothub@localhost:5432/iothub",
)

# asyncio's thread-pool executor runs getaddrinfo in worker threads, which can
# fail for Docker service names on some host configurations. Resolve the
# hostname synchronously in the main thread (C resolver, no thread overhead)
# and substitute the IP so asyncpg never needs to do DNS at all.
_host_match = re.search(r"@([^:@/]+):", DATABASE_URL)
if _host_match:
    _host = _host_match.group(1)
    try:
        _ip = socket.gethostbyname(_host)
        DATABASE_URL = (
            DATABASE_URL[: _host_match.start(1)] + _ip + DATABASE_URL[_host_match.end(1) :]
        )
    except OSError:
        pass  # keep original URL if DNS fails (e.g. local dev with localhost)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no live DB connection)."""
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


async def run_async_migrations() -> None:
    connectable = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
