"""Confirm-class requests: pending list, decide, WebSocket stream."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.backend.db import get_session
from hub.backend.models import ConfirmRequest
from hub.backend.schemas.confirm import ConfirmRequestOut, DecideBody

router = APIRouter(tags=["confirm"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/api/confirm/pending", response_model=list[ConfirmRequestOut])
async def get_pending(session: SessionDep) -> list[ConfirmRequestOut]:
    res = await session.execute(
        select(ConfirmRequest)
        .where(ConfirmRequest.state == "pending")
        .order_by(ConfirmRequest.created_at.asc())
    )
    return [ConfirmRequestOut.model_validate(r) for r in res.scalars()]


@router.get("/api/confirm/{confirm_id}", response_model=ConfirmRequestOut)
async def get_confirm(confirm_id: str, session: SessionDep) -> ConfirmRequestOut:
    try:
        uid = uuid.UUID(confirm_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid UUID") from exc
    req = await session.get(ConfirmRequest, uid)
    if req is None:
        raise HTTPException(status_code=404, detail="Confirm request not found")
    return ConfirmRequestOut.model_validate(req)


@router.post("/api/confirm/{confirm_id}/decide", response_model=ConfirmRequestOut)
async def decide(confirm_id: str, body: DecideBody, session: SessionDep) -> ConfirmRequestOut:
    try:
        uid = uuid.UUID(confirm_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid UUID") from exc

    req = await session.get(ConfirmRequest, uid)
    if req is None:
        raise HTTPException(status_code=404, detail="Confirm request not found")
    if req.state != "pending":
        raise HTTPException(status_code=409, detail=f"Request already in state '{req.state}'")

    req.state = "approved" if body.decision == "approve" else "rejected"
    req.decided_at = datetime.now(UTC)
    req.decided_by = "user"
    await session.commit()

    # Publish result to Redis so orchestrator can react
    try:
        redis = session.get_bind().sync_engine.pool  # type: ignore[attr-defined]
    except Exception:
        redis = None

    if redis is not None:
        try:
            from hub.backend.main import app  # type: ignore[import]

            r = app.state.redis
            await r.publish("confirm:result", json.dumps({"id": confirm_id, "state": req.state}))
        except Exception:
            pass

    return ConfirmRequestOut.model_validate(req)


@router.websocket("/ws/confirm")
async def ws_confirm(websocket: WebSocket, session: SessionDep) -> None:
    """Stream pending confirm requests and state changes."""
    await websocket.accept()
    try:
        redis = websocket.app.state.redis
        pubsub = redis.pubsub()
        await pubsub.subscribe("confirm:request", "confirm:result")

        # Send current pending list on connect
        res = await session.execute(select(ConfirmRequest).where(ConfirmRequest.state == "pending"))
        for req in res.scalars():
            await websocket.send_text(ConfirmRequestOut.model_validate(req).model_dump_json())

        try:
            while True:
                msg = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True), timeout=30
                )
                if msg and msg["type"] == "message":
                    await websocket.send_text(msg["data"])
                else:
                    await websocket.send_text('{"type":"ping"}')
        finally:
            await pubsub.unsubscribe("confirm:request", "confirm:result")
            await pubsub.aclose()
    except WebSocketDisconnect:
        pass
