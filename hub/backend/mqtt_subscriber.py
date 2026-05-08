"""Async MQTT → PostgreSQL subscriber running inside FastAPI lifespan."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any

import aiomqtt
from prometheus_client import Counter

from hub.backend.config import settings
from hub.backend.db import AsyncSessionLocal
from hub.backend.models import Event

logger = logging.getLogger(__name__)

MQTT_MSGS = Counter(
    "iot_hub_mqtt_msgs_total",
    "MQTT messages received",
    ["topic", "status"],
)

SUBSCRIPTIONS = [
    "home/+/sensors",
    "home/+/alert",
    "home/+/camera/event",
]

_DEAD_LETTER_KEY = "mqtt:dead-letter"
_DEAD_LETTER_MAX = 1000

_RedisClient = Any


async def run(redis_client: _RedisClient) -> None:
    while True:
        try:
            async with aiomqtt.Client(settings.mqtt_host, settings.mqtt_port) as client:
                for topic in SUBSCRIPTIONS:
                    await client.subscribe(topic)
                async for message in client.messages:
                    await _handle(message, redis_client)
        except aiomqtt.MqttError as exc:
            logger.warning("MQTT connection lost: %s — reconnecting in 5s", exc)
            await asyncio.sleep(5)


async def _handle(
    message: aiomqtt.Message,
    redis_client: _RedisClient,
) -> None:
    topic_str = str(message.topic)
    parts = topic_str.split("/")
    room = parts[1] if len(parts) >= 3 else None
    type_ = "/".join(parts[2:]) if len(parts) >= 3 else topic_str

    raw = message.payload if isinstance(message.payload, str | bytes) else b""

    try:
        payload: dict[str, Any] = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        await _dead_letter(redis_client, topic_str, raw, str(exc))
        MQTT_MSGS.labels(topic=type_, status="dead_letter").inc()
        return

    tier_raw = payload.get("tier", 1)
    try:
        tier = int(tier_raw)
    except (TypeError, ValueError):
        tier = -1

    if tier not in (0, 1, 2, 3):
        await _dead_letter(redis_client, topic_str, raw, f"invalid tier: {tier_raw!r}")
        MQTT_MSGS.labels(topic=type_, status="dead_letter").inc()
        return

    event = Event(
        timestamp=datetime.now(UTC),
        room=room,
        type=type_,
        tier=tier,
        payload=payload,
        model_version=payload.get("model_version"),
    )

    async with AsyncSessionLocal() as session:
        session.add(event)
        await session.commit()
        await session.refresh(event)

    # Publish to WebSocket subscribers
    await redis_client.publish(
        "events:new",
        json.dumps(
            {
                "id": str(event.id),
                "timestamp": event.timestamp.isoformat(),
                "room": event.room,
                "type": type_,
                "tier": tier,
                "payload": payload,
                "model_version": None,
            }
        ),
    )

    MQTT_MSGS.labels(topic=type_, status="ok").inc()


async def _dead_letter(
    redis_client: _RedisClient,
    topic: str,
    payload_raw: str | bytes,
    error: str,
) -> None:
    try:
        raw_str = (
            payload_raw.decode("utf-8", errors="replace")
            if isinstance(payload_raw, bytes)
            else payload_raw
        )
        entry = json.dumps({"topic": topic, "payload": raw_str, "error": error})
        lpush_result = redis_client.lpush(_DEAD_LETTER_KEY, entry)
        if isinstance(lpush_result, Awaitable):
            await lpush_result
        ltrim_result = redis_client.ltrim(_DEAD_LETTER_KEY, 0, _DEAD_LETTER_MAX - 1)
        if isinstance(ltrim_result, Awaitable):
            await ltrim_result
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to push to dead-letter list: %s", exc)
