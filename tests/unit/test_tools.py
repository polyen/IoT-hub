from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hub.edge.agent.tools import (
    ask_user,
    get_home_state,
    mqtt_publish,
    send_push,
    set_timer,
    summarize_period,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def make_redis() -> AsyncMock:
    mock = AsyncMock()
    return mock


# ── 1: get_home_state returns correct room data ───────────────────────────────


@pytest.mark.asyncio
async def test_get_home_state_returns_room_data() -> None:
    redis = make_redis()
    redis.keys.return_value = [b"home:state:kitchen"]
    redis.hgetall.return_value = {b"temperature": b"22", b"humidity": b"55"}
    result = await get_home_state(redis)
    assert "kitchen" in result
    assert result["kitchen"] == {b"temperature": b"22", b"humidity": b"55"}


# ── 2: get_home_state with room filter uses correct key pattern ───────────────


@pytest.mark.asyncio
async def test_get_home_state_room_filter_pattern() -> None:
    redis = make_redis()
    redis.keys.return_value = [b"home:state:bedroom"]
    redis.hgetall.return_value = {}
    await get_home_state(redis, room="bedroom")
    redis.keys.assert_called_once_with("home:state:bedroom")


# ── 3: set_timer stores key in Redis with correct TTL ─────────────────────────


@pytest.mark.asyncio
async def test_set_timer_stores_with_ttl() -> None:
    redis = make_redis()
    await set_timer(redis, duration_sec=120, label="pizza")
    redis.setex.assert_called_once()
    args = redis.setex.call_args[0]
    assert args[1] == 120
    assert "pizza" in args[2]


# ── 4: set_timer returns timer_id ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_timer_returns_timer_id() -> None:
    redis = make_redis()
    result = await set_timer(redis, duration_sec=60)
    assert "timer_id" in result
    assert len(result["timer_id"]) == 8
    assert result["duration_sec"] == 60


# ── 5: send_push calls ntfy with correct headers ──────────────────────────────


@pytest.mark.asyncio
async def test_send_push_correct_headers() -> None:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("hub.edge.agent.tools.httpx.AsyncClient", return_value=mock_client):
        result = await send_push(
            "http://ntfy.example.com/alerts",
            title="Fire!",
            message="Kitchen fire detected",
            priority="high",
        )
    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    headers = call_kwargs[1]["headers"]
    assert headers["Title"] == "Fire!"
    assert headers["Priority"] == "high"
    assert result["status"] == "sent"


# ── 6: ask_user publishes to Redis channel ────────────────────────────────────


@pytest.mark.asyncio
async def test_ask_user_publishes_to_channel() -> None:
    redis = make_redis()
    result = await ask_user(redis, "What temperature do you prefer?")
    redis.publish.assert_called_once()
    args = redis.publish.call_args[0]
    assert args[0] == "agent:ask_user"
    payload = json.loads(args[1])
    assert payload["question"] == "What temperature do you prefer?"
    assert result["status"] == "sent"


# ── 7: mqtt_publish publishes JSON to correct topic ───────────────────────────


@pytest.mark.asyncio
async def test_mqtt_publish_correct_topic() -> None:
    mqtt = AsyncMock()
    result = await mqtt_publish(mqtt, "home/kitchen/light", {"state": "on"})
    mqtt.publish.assert_called_once()
    args = mqtt.publish.call_args[0]
    assert args[0] == "home/kitchen/light"
    published_payload = json.loads(args[1])
    assert published_payload == {"state": "on"}
    assert result["status"] == "published"
    assert result["topic"] == "home/kitchen/light"


# ── 8: query_events_db returns formatted dicts ────────────────────────────────


@pytest.mark.asyncio
async def test_query_events_db_returns_dicts() -> None:
    import uuid

    session = AsyncMock()
    mock_event = MagicMock()
    mock_event.id = uuid.uuid4()
    mock_event.type = "motion"
    mock_event.room = "hallway"
    mock_event.timestamp = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    scalars_result = MagicMock()
    scalars_result.all.return_value = [mock_event]
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars_result
    session.execute = AsyncMock(return_value=execute_result)

    from hub.edge.agent.tools import query_events_db

    results = await query_events_db(session, query="motion events", limit=5)
    assert len(results) == 1
    assert results[0]["type"] == "motion"
    assert results[0]["room"] == "hallway"
    assert "id" in results[0]
    assert "timestamp" in results[0]


# ── 9: summarize_period calculates correct time range ────────────────────────


@pytest.mark.asyncio
async def test_summarize_period_today() -> None:
    session = AsyncMock()
    rows_result = MagicMock()
    rows_result.all.return_value = [("motion", 5), ("fire", 1)]
    execute_result = MagicMock()
    execute_result.all = rows_result.all
    session.execute = AsyncMock(return_value=execute_result)

    result = await summarize_period(session, period="today")
    assert result["period"] == "today"
    assert "event_counts" in result


# ── 10: send_push raises on HTTP error ───────────────────────────────────────


@pytest.mark.asyncio
async def test_send_push_raises_on_http_error() -> None:
    import httpx

    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Error", request=MagicMock(), response=MagicMock()
    )
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("hub.edge.agent.tools.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(httpx.HTTPStatusError):
            await send_push("http://ntfy.example.com/alerts", title="Test", message="msg")
