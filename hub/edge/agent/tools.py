from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx
import redis.asyncio as aioredis

from hub.edge.agent.policy import ToolCall  # noqa: F401

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "get_home_state": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "room": {"type": "string"},
        },
    },
    "query_events_db": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            "since_hours": {"type": "number", "default": 24},
        },
        "required": ["query"],
    },
    "set_timer": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "duration_sec": {"type": "integer", "minimum": 1, "maximum": 86400},
            "label": {"type": "string"},
        },
        "required": ["duration_sec"],
    },
    "send_push": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string"},
            "message": {"type": "string"},
            "priority": {"type": "string", "enum": ["default", "high", "urgent"]},
        },
        "required": ["title", "message"],
    },
    "summarize_period": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "period": {"type": "string", "enum": ["today", "yesterday", "week"]},
        },
        "required": ["period"],
    },
    "ask_user": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "question": {"type": "string"},
        },
        "required": ["question"],
    },
    "mqtt_publish": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "topic": {"type": "string"},
            "payload": {"type": "object"},
        },
        "required": ["topic", "payload"],
    },
}


async def get_home_state(
    redis_client: aioredis.Redis,
    room: str | None = None,
) -> dict[str, Any]:
    pattern = f"home:state:{room}" if room else "home:state:*"
    keys = await redis_client.keys(pattern)
    result: dict[str, Any] = {}
    for key in keys:
        k = key if isinstance(key, str) else key.decode()
        data: dict[Any, Any] = await redis_client.hgetall(k)  # type: ignore[misc]
        room_name = k.split(":")[-1]
        result[room_name] = data
    return result


async def query_events_db(
    session: Any,
    query: str,
    limit: int = 10,
    since_hours: float = 24.0,
) -> list[dict[str, Any]]:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from hub.backend.models import Event

    since = datetime.now(UTC) - timedelta(hours=since_hours)
    stmt = (
        select(Event).where(Event.timestamp >= since).order_by(Event.timestamp.desc()).limit(limit)
    )
    result = await session.execute(stmt)
    events = result.scalars().all()
    return [
        {
            "id": str(e.id),
            "type": e.type,
            "room": e.room,
            "timestamp": e.timestamp.isoformat(),
        }
        for e in events
    ]


async def set_timer(
    redis_client: aioredis.Redis,
    duration_sec: int,
    label: str = "timer",
) -> dict[str, Any]:
    timer_id = str(uuid.uuid4())[:8]
    expires_at = time.time() + duration_sec
    await redis_client.setex(f"timer:{timer_id}", duration_sec, f"{label}|{expires_at}")
    return {"timer_id": timer_id, "label": label, "duration_sec": duration_sec}


async def send_push(
    ntfy_url: str,
    title: str,
    message: str,
    priority: str = "default",
) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            ntfy_url,
            headers={"Title": title, "Priority": priority},
            content=message,
            timeout=5.0,
        )
        resp.raise_for_status()
    return {"status": "sent", "title": title}


async def summarize_period(
    session: Any,
    period: str = "today",
) -> dict[str, Any]:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func, select

    from hub.backend.models import Event

    now = datetime.now(UTC)
    since: datetime
    if period == "today":
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "yesterday":
        since = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        now = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        since = now - timedelta(days=7)
    stmt = select(Event.type, func.count()).where(Event.timestamp >= since).group_by(Event.type)
    result = await session.execute(stmt)
    counts = {row[0]: row[1] for row in result.all()}
    return {"period": period, "event_counts": counts}


async def ask_user(
    redis_client: aioredis.Redis,
    question: str,
) -> dict[str, Any]:
    await redis_client.publish("agent:ask_user", json.dumps({"question": question}))
    return {"status": "sent", "question": question}


async def mqtt_publish(
    mqtt_client: Any,
    topic: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    await mqtt_client.publish(topic, json.dumps(payload))
    return {"status": "published", "topic": topic}
