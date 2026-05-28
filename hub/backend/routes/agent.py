"""Agent audit log, try-command endpoint, voice WebSocket stream."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.backend.db import get_session
from hub.backend.models import AgentAudit
from hub.backend.services.policy_loader import simulate

router = APIRouter(prefix="/api/agent", tags=["agent"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class AuditOut(BaseModel):
    id: UUID
    timestamp: datetime
    intent_text: str
    tool: str | None
    action_class: str
    executed: bool
    confirmation: str | None
    latency_ms: int | None
    llm_version: str | None

    model_config = {"from_attributes": True}


class TryBody(BaseModel):
    intent_text: str
    tool: str | None = None
    payload: dict[str, Any] | None = None


class TryResult(BaseModel):
    matched_rule: str
    action_class: str
    reason: str
    latency_ms: int
    inferred_tool: str | None = None


@router.get("/audit", response_model=list[AuditOut])
async def get_audit(
    session: SessionDep,
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
) -> list[AgentAudit]:
    res = await session.execute(
        select(AgentAudit).order_by(AgentAudit.timestamp.desc()).limit(limit).offset(offset)
    )
    return list(res.scalars())


@router.post("/try", response_model=TryResult)
async def try_command(body: TryBody, session: SessionDep) -> TryResult:
    t0 = time.perf_counter()
    result = simulate(body.intent_text, body.tool, body.payload)
    latency_ms = max(1, int((time.perf_counter() - t0) * 1000))

    entry = AgentAudit(
        timestamp=datetime.now(UTC),
        intent_text=body.intent_text,
        tool=body.tool,
        action_class=result["class"],
        executed=False,
        latency_ms=latency_ms,
    )
    session.add(entry)
    await session.commit()

    return TryResult(
        matched_rule=result["matched_rule"],
        action_class=result["class"],
        reason=result["reason"],
        latency_ms=latency_ms,
        inferred_tool=result.get("inferred_tool"),
    )


@router.websocket("/ws/voice")
async def voice_ws(websocket: WebSocket) -> None:
    """Stream voice transcripts and wake-word events from Redis pub/sub."""
    await websocket.accept()
    from hub.backend.main import app  # noqa: PLC0415

    pubsub = app.state.redis.pubsub()
    await pubsub.subscribe("voice:transcript", "voice:wakeword")
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await pubsub.unsubscribe("voice:transcript", "voice:wakeword")
        await pubsub.aclose()


@router.websocket("/ws/agent")
async def agent_ws(websocket: WebSocket) -> None:
    """Stream live agent turn updates: intent → tool plan → results."""
    await websocket.accept()
    from hub.backend.main import app  # noqa: PLC0415

    pubsub = app.state.redis.pubsub()
    await pubsub.subscribe("agent:turn", "agent:tool_call", "agent:result")
    try:
        while True:
            msg = await asyncio.wait_for(
                pubsub.get_message(ignore_subscribe_messages=True), timeout=30
            )
            if msg and msg["type"] == "message":
                await websocket.send_text(msg["data"])
            else:
                await websocket.send_text('{"type":"ping"}')
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await pubsub.unsubscribe("agent:turn", "agent:tool_call", "agent:result")
        await pubsub.aclose()


@router.get("/history")
async def get_agent_history(request: Request) -> list[dict[str, Any]]:
    """Return last 100 agent turn events for Stack tab hydration on page load."""
    import json as _json  # noqa: PLC0415

    redis = request.app.state.redis
    items: list[str] = await redis.lrange("agent:history", 0, 99)
    return [_json.loads(item) for item in reversed(items)]


@router.post("/run")
async def run_intent(body: TryBody, request: Request, session: SessionDep) -> dict[str, str]:
    """Queue an intent text for the edge orchestrator via MQTT voice/command."""

    import json as _json  # noqa: PLC0415

    redis = request.app.state.redis
    await redis.publish("mqtt:publish:voice/command", _json.dumps({"text": body.intent_text}))

    entry = AgentAudit(
        timestamp=datetime.now(UTC),
        intent_text=body.intent_text,
        tool=body.tool,
        action_class="AUTO",
        executed=True,
        latency_ms=None,
    )
    session.add(entry)
    await session.commit()
    return {"result": "queued", "id": str(entry.id)}


class DisambiguateBody(BaseModel):
    intent_text: str
    chosen_device_id: str


@router.post("/disambiguate")
async def disambiguate(body: DisambiguateBody, request: Request) -> dict[str, str]:
    """Re-run a voice intent forcing a specific device, bypassing ambiguity.

    The UI calls this after the user taps a candidate device in the AmbiguityResolver.
    """
    import json as _json  # noqa: PLC0415

    redis = request.app.state.redis
    await redis.publish(
        "mqtt:publish:voice/command",
        _json.dumps({"text": body.intent_text, "forced_device_id": body.chosen_device_id}),
    )
    return {"result": "queued"}


@router.post("/voice/audio")
async def submit_voice_audio(websocket_request: Request) -> dict[str, Any]:
    """Receive audio blob from PTT, store in Redis, notify voice pipeline.

    Accepts either:
    - raw audio bytes with audio/* Content-Type (new frontend)
    - multipart/form-data with an 'audio' field (legacy frontend)
    """
    from hub.backend.main import app  # noqa: PLC0415

    body = await websocket_request.body()
    if not body:
        raise HTTPException(status_code=422, detail="Empty audio body")
    redis = app.state.redis

    # Store blob under a unique key (TTL 5 min — voice pipeline must consume before expiry)
    blob_id = str(uuid.uuid4())
    blob_key = f"voice:audio_blob:{blob_id}"
    await redis.set(blob_key, body, ex=300)

    # Notify voice pipeline via stream (consumer reads blob_key, processes, deletes)
    await redis.xadd(
        "voice:audio_stream",
        {"blob_key": blob_key, "blob_size": len(body), "source": "ptt"},
        maxlen=100,
    )
    return {"status": "queued", "blob_id": blob_id, "bytes": len(body)}
