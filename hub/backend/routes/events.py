"""WebSocket events feed + feedback endpoint."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.backend.db import get_session
from hub.backend.models import Event, FeedbackEvent

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.websocket("/ws/events")
async def ws_events(websocket: WebSocket, session: SessionDep) -> None:
    """Stream new events to connected PWA clients.

    Accepts optional ?since=<event_id> to replay missed events on reconnect.
    """
    await websocket.accept()
    since_id = websocket.query_params.get("since")

    try:
        # Replay missed events if since_id provided
        if since_id:
            stmt = select(Event).order_by(Event.timestamp.asc()).limit(50)
            result = await session.execute(stmt)
            for event in result.scalars():
                await websocket.send_text(json.dumps(_event_to_dict(event)))

        # Live stream via Redis pub/sub
        redis = websocket.app.state.redis
        pubsub = redis.pubsub()
        await pubsub.subscribe("events:new")

        try:
            while True:
                message = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True), timeout=30
                )
                if message and message["type"] == "message":
                    await websocket.send_text(message["data"])
                else:
                    # keepalive ping — client ignores frames without an id
                    await websocket.send_text('{"type":"ping"}')
        finally:
            await pubsub.unsubscribe("events:new")
            await pubsub.aclose()

    except WebSocketDisconnect:
        pass


@router.post("/api/feedback")
async def submit_feedback(
    body: dict[str, Any],
    session: SessionDep,
) -> dict[str, str]:
    alert_id_raw = body.get("alert_id")
    user_label = body.get("user_label")
    if not alert_id_raw or not user_label:
        raise HTTPException(status_code=422, detail="alert_id and user_label required")

    try:
        alert_uuid = uuid.UUID(str(alert_id_raw))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="alert_id must be a valid UUID") from exc

    fb = FeedbackEvent(
        alert_id=alert_uuid,
        user_label=str(user_label),
        tag=body.get("tag"),
        ts=datetime.now(UTC),
    )
    session.add(fb)
    await session.commit()
    return {"status": "ok"}


def _event_to_dict(event: Event) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "timestamp": event.timestamp.isoformat(),
        "room": event.room,
        "type": event.type,
        "tier": event.tier,
        "payload": event.payload,
        "model_version": event.model_version,
    }
