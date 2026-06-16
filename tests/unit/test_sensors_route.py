"""Unit tests for hub.backend.routes.sensors."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from hub.backend.routes.sensors import latest, timeseries


class _FakeRedis:
    def __init__(self, data: dict[str, dict[str, str]]) -> None:
        self._data = data

    async def scan_iter(self, match: str | None = None) -> Any:
        for key in self._data:
            yield key

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._data[key])


def _request_with_redis(redis: _FakeRedis) -> Any:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis)))


@pytest.mark.asyncio
async def test_latest_parses_values_and_strips_ts() -> None:
    redis = _FakeRedis(
        {
            "home:climate:living-room": {
                "temperature": "22.5",
                "humidity": "48",
                "ts": "2026-06-16T10:00:00+00:00",
            },
            "home:climate:kitchen": {"power_w": "120", "ts": "2026-06-16T10:00:01+00:00"},
        }
    )
    out = await latest(_request_with_redis(redis))  # type: ignore[arg-type]

    assert set(out.rooms) == {"living-room", "kitchen"}
    living = out.rooms["living-room"]
    assert living.values == {"temperature": 22.5, "humidity": 48.0}
    assert living.ts == "2026-06-16T10:00:00+00:00"
    assert out.rooms["kitchen"].values == {"power_w": 120.0}


@pytest.mark.asyncio
async def test_timeseries_rejects_bad_range() -> None:
    with pytest.raises(HTTPException) as exc:
        await timeseries(AsyncMock(), room="living-room", fields="temperature", range="99y")
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_timeseries_rejects_injection_field() -> None:
    with pytest.raises(HTTPException) as exc:
        await timeseries(
            AsyncMock(),
            room="living-room",
            fields="temperature; DROP TABLE events",
            range="24h",
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_timeseries_aggregates_rows() -> None:
    t0 = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    rows = [
        {"t": t0, "temperature": 21.0, "humidity": 50.0},
        {"t": t0, "temperature": None, "humidity": None},  # dropped (all null)
    ]

    class _Result:
        def mappings(self) -> Any:
            return rows

    session = AsyncMock()
    session.execute = AsyncMock(return_value=_Result())

    out = await timeseries(
        session, room="living-room", fields="temperature,humidity,temperature", range="1h"
    )

    # duplicate field collapsed, both real fields kept
    assert out.fields == ["temperature", "humidity"]
    assert out.bucket == "1 minute"
    assert len(out.points) == 1
    assert out.points[0].values == {"temperature": 21.0, "humidity": 50.0}
