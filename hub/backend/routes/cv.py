"""Camera registry and CV detection WebSocket."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.backend.db import get_session
from hub.backend.models import DevicePlacement
from hub.backend.schemas.cv import CameraOut

router = APIRouter(tags=["cv"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/api/cv/cameras", response_model=list[CameraOut])
async def list_cameras(session: SessionDep) -> list[CameraOut]:
    """Return cameras derived from DevicePlacement records where kind='camera'."""
    res = await session.execute(select(DevicePlacement).where(DevicePlacement.kind == "camera"))
    cameras: list[CameraOut] = []
    for p in res.scalars():
        cfg: dict = p.config or {}
        cameras.append(
            CameraOut(
                id=str(p.id),
                name=p.label or p.device_id,
                stream_hls=cfg.get("stream_hls"),
                stream_webrtc=cfg.get("stream_webrtc"),
                online=cfg.get("online", False),
            )
        )
    return cameras


@router.websocket("/ws/cv/{camera_id}")
async def ws_cv(camera_id: str, websocket: WebSocket) -> None:
    """Stream CV detections for a specific camera from Redis pub/sub."""
    await websocket.accept()
    try:
        redis = websocket.app.state.redis
        pubsub = redis.pubsub()
        channel = f"cv:detections:{camera_id}"
        await pubsub.subscribe(channel)
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
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
    except WebSocketDisconnect:
        pass
