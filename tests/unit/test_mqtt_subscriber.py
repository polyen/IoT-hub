"""Unit tests for hub.backend.mqtt_subscriber."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import hub.backend.mqtt_subscriber as mqtt_subscriber
from hub.backend.mqtt_subscriber import MQTT_MSGS, _dead_letter, _handle


class _FakeMessage:
    def __init__(self, topic: str, payload: bytes | str) -> None:
        self.topic = _FakeTopic(topic)
        self.payload = payload


class _FakeTopic:
    def __init__(self, value: str) -> None:
        self._value = value

    def __str__(self) -> str:
        return self._value


def _make_redis() -> AsyncMock:
    r = AsyncMock()
    r.lpush = AsyncMock(return_value=1)
    r.ltrim = AsyncMock(return_value=None)
    return r


@pytest.fixture(autouse=True)
def _reset_dedup_state() -> None:
    """Clear the module-level dedup caches so tests don't leak event-suppression
    state into each other.  ``_handle`` routes sensors/fused events through
    ``_is_event_suppressed``, which records ``{room}/{type_}`` in ``_seen_events``
    with a 60s TTL; without this reset a prior test publishing the same
    room+type would cause a later ``_handle`` call to be suppressed (no DB
    write), making assertions order-dependent."""
    mqtt_subscriber._seen_events.clear()
    mqtt_subscriber._seen_tracks.clear()


@pytest.mark.asyncio
async def test_handle_valid_sensors_message() -> None:
    payload: dict[str, Any] = {
        "tier": 1,
        "temperature": 22.5,
        "humidity": 55,
        "model_version": "v1.0",
    }
    message = _FakeMessage("home/living-room/sensors", json.dumps(payload).encode())
    redis_client = _make_redis()

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    with patch("hub.backend.mqtt_subscriber.AsyncSessionLocal", return_value=mock_session):
        before = _get_counter_value("sensors", "ok")
        await _handle(message, redis_client)
        after = _get_counter_value("sensors", "ok")

    mock_session.add.assert_called_once()
    mock_session.commit.assert_awaited_once()
    assert after == before + 1.0

    added_event = mock_session.add.call_args[0][0]
    assert added_event.room == "living-room"
    assert added_event.type == "sensors"
    assert added_event.tier == 1
    assert added_event.model_version == "v1.0"


@pytest.mark.asyncio
async def test_handle_invalid_json() -> None:
    message = _FakeMessage("home/kitchen/sensors", b"not-valid-json{{{")
    redis_client = _make_redis()

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.add = MagicMock()

    with patch("hub.backend.mqtt_subscriber.AsyncSessionLocal", return_value=mock_session):
        before = _get_counter_value("sensors", "dead_letter")
        await _handle(message, redis_client)
        after = _get_counter_value("sensors", "dead_letter")

    mock_session.add.assert_not_called()
    assert after == before + 1.0
    redis_client.lpush.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_invalid_tier() -> None:
    payload: dict[str, Any] = {"tier": 99, "temperature": 22.5}
    message = _FakeMessage("home/bedroom/sensors", json.dumps(payload).encode())
    redis_client = _make_redis()

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.add = MagicMock()

    with patch("hub.backend.mqtt_subscriber.AsyncSessionLocal", return_value=mock_session):
        before = _get_counter_value("sensors", "dead_letter")
        await _handle(message, redis_client)
        after = _get_counter_value("sensors", "dead_letter")

    mock_session.add.assert_not_called()
    assert after == before + 1.0
    redis_client.lpush.assert_awaited_once()


@pytest.mark.asyncio
async def test_dead_letter_called_on_bad_json() -> None:
    message = _FakeMessage("home/garage/alert", b"}{invalid")
    redis_client = _make_redis()

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.add = MagicMock()

    with patch("hub.backend.mqtt_subscriber.AsyncSessionLocal", return_value=mock_session):
        await _handle(message, redis_client)

    redis_client.lpush.assert_awaited_once()
    call_args = redis_client.lpush.call_args
    key = call_args[0][0]
    entry = call_args[0][1]
    assert key == "mqtt:dead-letter"
    parsed = json.loads(entry)
    assert parsed["topic"] == "home/garage/alert"
    assert "error" in parsed
    redis_client.ltrim.assert_awaited_once()


@pytest.mark.asyncio
async def test_dead_letter_helper_directly() -> None:
    redis_client = _make_redis()
    await _dead_letter(redis_client, "home/test/sensors", b'{"x": 1}', "some error")
    redis_client.lpush.assert_awaited_once()
    redis_client.ltrim.assert_awaited_once()

    ltrim_args = redis_client.ltrim.call_args[0]
    assert ltrim_args[0] == "mqtt:dead-letter"
    assert ltrim_args[1] == 0
    assert ltrim_args[2] == 999


def _get_counter_value(topic: str, status: str) -> float:
    try:
        return MQTT_MSGS.labels(topic=topic, status=status)._value.get()
    except Exception:
        return 0.0
