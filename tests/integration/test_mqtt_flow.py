"""Integration tests for the MQTT subscriber flow.

These tests exercise hub.backend.mqtt_subscriber._handle end-to-end with mocked
aiomqtt messages, a mocked AsyncSessionLocal (so no real PostgreSQL is needed),
and an AsyncMock Redis client. They verify:

  1. A valid sensor JSON message → Event row added & committed; Redis publish.
  2. An invalid JSON payload     → no DB insert; entry pushed to Redis
                                   dead-letter list `mqtt:dead-letter`.
  3. A payload with invalid tier → no DB insert; dead-letter entry.

A real Mosquitto broker is not required — aiomqtt.Message instances are
constructed directly via lightweight stand-in classes.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hub.backend.mqtt_subscriber import _handle

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Lightweight aiomqtt.Message stand-ins
# ---------------------------------------------------------------------------


class _FakeTopic:
    def __init__(self, value: str) -> None:
        self._value = value

    def __str__(self) -> str:
        return self._value


class _FakeMessage:
    def __init__(self, topic: str, payload: bytes | str) -> None:
        self.topic = _FakeTopic(topic)
        self.payload = payload


def _make_redis() -> AsyncMock:
    r = AsyncMock()
    r.lpush = AsyncMock(return_value=1)
    r.ltrim = AsyncMock(return_value=None)
    r.publish = AsyncMock(return_value=1)
    return r


def _make_session() -> AsyncMock:
    s = AsyncMock()
    s.__aenter__ = AsyncMock(return_value=s)
    s.__aexit__ = AsyncMock(return_value=None)
    s.add = MagicMock()
    s.commit = AsyncMock()
    s.refresh = AsyncMock()
    return s


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mqtt_valid_sensor_inserts_event() -> None:
    """Valid JSON on home/+/sensors → Event added & committed; nothing to dead-letter."""
    payload: dict[str, Any] = {
        "tier": 1,
        "temperature": 21.4,
        "humidity": 47,
        "model_version": "sensors-v1",
    }
    message = _FakeMessage("home/kitchen/sensors", json.dumps(payload).encode())
    redis_client = _make_redis()
    session = _make_session()

    with patch("hub.backend.mqtt_subscriber.AsyncSessionLocal", return_value=session):
        await _handle(message, redis_client)

    # DB write happened
    session.add.assert_called_once()
    session.commit.assert_awaited_once()

    # Inserted Event reflects the topic & payload
    event = session.add.call_args[0][0]
    assert event.room == "kitchen"
    assert event.type == "sensors"
    assert event.tier == 1
    assert event.payload == payload
    assert event.model_version == "sensors-v1"

    # Redis publish on events:new (websocket fan-out), no dead-letter
    redis_client.publish.assert_awaited_once()
    assert redis_client.publish.call_args[0][0] == "events:new"
    redis_client.lpush.assert_not_awaited()


@pytest.mark.asyncio
async def test_mqtt_invalid_json_dead_letters() -> None:
    """Invalid JSON → no DB insert; pushed to mqtt:dead-letter Redis list."""
    message = _FakeMessage("home/kitchen/sensors", b"{not-valid-json")
    redis_client = _make_redis()
    session = _make_session()

    with patch("hub.backend.mqtt_subscriber.AsyncSessionLocal", return_value=session):
        await _handle(message, redis_client)

    # No DB insert
    session.add.assert_not_called()
    session.commit.assert_not_awaited()

    # One dead-letter push to the documented key
    redis_client.lpush.assert_awaited_once()
    key, raw_entry = redis_client.lpush.call_args[0]
    assert key == "mqtt:dead-letter"

    parsed = json.loads(raw_entry)
    assert parsed["topic"] == "home/kitchen/sensors"
    assert "error" in parsed and parsed["error"]
    assert "payload" in parsed

    # ltrim caps the dead-letter list size
    redis_client.ltrim.assert_awaited_once()


@pytest.mark.asyncio
async def test_mqtt_invalid_tier_dead_letters() -> None:
    """tier outside {0,1,2,3} → dead-letter, no DB insert."""
    payload: dict[str, Any] = {"tier": 7, "temperature": 22.0}
    message = _FakeMessage("home/bedroom/sensors", json.dumps(payload).encode())
    redis_client = _make_redis()
    session = _make_session()

    with patch("hub.backend.mqtt_subscriber.AsyncSessionLocal", return_value=session):
        await _handle(message, redis_client)

    session.add.assert_not_called()
    redis_client.lpush.assert_awaited_once()
    key, raw_entry = redis_client.lpush.call_args[0]
    assert key == "mqtt:dead-letter"
    parsed = json.loads(raw_entry)
    assert "invalid tier" in parsed["error"]
