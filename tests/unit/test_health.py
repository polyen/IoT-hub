from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from hub.backend.db import get_session
from hub.backend.main import app


class _FakeRedis:
    async def ping(self) -> str:
        return "PONG"

    async def aclose(self) -> None:
        pass


class _FailRedis:
    async def ping(self) -> None:
        raise ConnectionError("redis down")

    async def aclose(self) -> None:
        pass


async def _ok_session() -> AsyncGenerator[AsyncSession, None]:
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(return_value=None)
    yield session


async def _fail_session() -> AsyncGenerator[AsyncSession, None]:
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(side_effect=OSError("pg down"))
    yield session


@pytest.fixture()
def client_live() -> TestClient:
    app.state.redis = _FakeRedis()
    app.dependency_overrides[get_session] = _ok_session
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def client_pg_fail() -> TestClient:
    app.state.redis = _FakeRedis()
    app.dependency_overrides[get_session] = _fail_session
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


def test_liveness_ok(client_live: TestClient) -> None:
    resp = client_live.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readiness_pg_fail_returns_503(client_pg_fail: TestClient) -> None:
    resp = client_pg_fail.get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["postgres"] == "fail"
