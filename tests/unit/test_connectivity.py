"""Unit tests for hub.edge.sync.connectivity."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import hub.edge.sync.connectivity as conn_mod
from hub.edge.sync.connectivity import ConnectivityMonitor


@pytest.fixture()
def monitor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ConnectivityMonitor:
    db = tmp_path / "telegram_queue.db"
    monkeypatch.setattr(conn_mod, "TELEGRAM_QUEUE_DB", db)
    return ConnectivityMonitor(
        vps_heartbeat_url="http://vps.example.com/health",
        mqtt_host="localhost",
        mqtt_port=1883,
    )


@pytest.mark.asyncio
async def test_ping_vps_up(monitor: ConnectivityMonitor) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("hub.edge.sync.connectivity.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        status, latency = await monitor._ping_vps()

    assert status == "up"
    assert isinstance(latency, int)
    assert latency >= 0


@pytest.mark.asyncio
async def test_ping_vps_down_on_connection_error(monitor: ConnectivityMonitor) -> None:
    with patch("hub.edge.sync.connectivity.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
        mock_client_cls.return_value = mock_client

        status, latency = await monitor._ping_vps()

    assert status == "down"
    assert latency is None


def test_queue_telegram_message_inserts(monitor: ConnectivityMonitor) -> None:
    import sqlite3

    payload = {"chat_id": 123, "text": "alert"}
    monitor.queue_telegram_message(payload)

    with sqlite3.connect(conn_mod.TELEGRAM_QUEUE_DB) as db:
        rows = db.execute("SELECT payload FROM queue").fetchall()

    assert len(rows) == 1
    assert json.loads(rows[0][0]) == payload


@pytest.mark.asyncio
async def test_flush_telegram_queue_calls_send_fn(monitor: ConnectivityMonitor) -> None:
    payloads = [{"chat_id": 1, "text": "first"}, {"chat_id": 2, "text": "second"}]
    for p in payloads:
        monitor.queue_telegram_message(p)

    received: list[dict] = []

    async def send_fn(payload: dict) -> None:
        received.append(payload)

    count = await monitor.flush_telegram_queue(send_fn)

    assert count == 2
    assert received == payloads


@pytest.mark.asyncio
async def test_flush_telegram_queue_returns_correct_count(monitor: ConnectivityMonitor) -> None:
    for i in range(3):
        monitor.queue_telegram_message({"i": i})

    async def send_fn(payload: dict) -> None:
        pass

    count = await monitor.flush_telegram_queue(send_fn)
    assert count == 3
