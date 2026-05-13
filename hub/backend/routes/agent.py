"""Agent audit log, try-command endpoint, voice WebSocket stream."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.backend.db import get_session
from hub.backend.models import AgentAudit
from hub.backend.services.policy_loader import simulate

router = APIRouter(prefix="/api/agent", tags=["agent"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class AuditOut(BaseModel):
    id: str
    timestamp: str
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
