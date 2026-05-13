"""Async SQLAlchemy engine and session factory."""

from __future__ import annotations

import socket
from collections.abc import AsyncGenerator

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from hub.backend.config import settings

# Resolve the DB hostname to an IP synchronously (C resolver, main thread) so
# asyncio's thread-pool executor never needs to do DNS for Docker service names.
_parsed = make_url(settings.database_url)
try:
    _ip = socket.gethostbyname(_parsed.host or "localhost")
    _resolved_url = URL.create(
        drivername=_parsed.drivername,
        username=_parsed.username,
        password=_parsed.password,
        host=_ip,
        port=_parsed.port,
        database=_parsed.database,
        query=_parsed.query,
    )
except OSError:
    _resolved_url = _parsed

engine = create_async_engine(
    _resolved_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async DB session."""
    async with AsyncSessionLocal() as session:
        yield session
