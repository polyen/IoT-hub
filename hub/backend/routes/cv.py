"""Camera registry, snapshot, and CV detection WebSocket."""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from hub.backend.config import settings
from hub.backend.db import AsyncSessionLocal, get_session
from hub.backend.models import DevicePlacement
from hub.backend.schemas.cv import CameraOut

router = APIRouter(tags=["cv"])


async def _mediamtx_path_ready(path_name: str) -> bool:
    """Return True if the MediaMTX path has an active source (ready=true)."""
    url = f"{settings.mediamtx_api}/v3/paths/get/{path_name}"
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get(url)
            return bool(r.status_code == 200 and r.json().get("ready", False))
    except Exception:
        return False


SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/api/cv/cameras", response_model=list[CameraOut])
async def list_cameras(session: SessionDep) -> list[CameraOut]:
    """Return cameras derived from DevicePlacement records where kind='camera'."""
    res = await session.execute(select(DevicePlacement).where(DevicePlacement.kind == "camera"))
    placements = list(res.scalars())

    # Check MediaMTX readiness for all cameras concurrently.
    # Path name defaults to device_id (matches the /hls/{device_id}/... URL).
    async def _camera_out(p: DevicePlacement) -> CameraOut:
        cfg: dict[str, Any] = p.config or {}
        mediamtx_path = cfg.get("mediamtx_path") or p.device_id
        stream_hls = cfg.get("stream_hls") or f"/hls/{mediamtx_path}/index.m3u8"
        online = await _mediamtx_path_ready(mediamtx_path)
        return CameraOut(
            id=str(p.id),
            name=p.label or p.device_id,
            stream_hls=stream_hls,
            stream_webrtc=cfg.get("stream_webrtc"),
            online=online,
        )

    cameras = await asyncio.gather(*(_camera_out(p) for p in placements))
    return list(cameras)


@router.post("/api/cv/cameras/{camera_id}/snapshot")
async def snapshot(camera_id: str, session: SessionDep) -> dict[str, Any]:
    """Return current frame URL for a camera (from config or MediaMTX)."""
    try:
        uid = uuid.UUID(camera_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid camera id") from exc
    res = await session.execute(
        select(DevicePlacement)
        .where(DevicePlacement.id == uid, DevicePlacement.kind == "camera")
        .limit(1)
    )
    placement = res.scalar_one_or_none()
    if not placement:
        raise HTTPException(status_code=404, detail="Camera not found")
    cfg: dict[str, Any] = placement.config or {}
    return {"camera_id": camera_id, "frame_url": cfg.get("snapshot_url")}


@router.get("/api/cv/pipeline-config")
async def pipeline_config(session: SessionDep) -> dict[str, Any]:
    """CV pipeline self-config: which room slug to publish detections under.

    The edge CV pipeline polls this so moving the camera between rooms in the
    floor-plan editor re-targets its MQTT room with no env change or restart.
    Single-camera assumption — returns the first camera placement.
    """
    res = await session.execute(
        select(DevicePlacement)
        .options(selectinload(DevicePlacement.room))
        .where(DevicePlacement.kind == "camera")
        .limit(1)
    )
    placement = res.scalar_one_or_none()
    if placement is None or placement.room is None:
        return {"room": None, "camera_id": None}
    cfg: dict[str, Any] = placement.config or {}
    return {
        "room": cfg.get("mqtt_room") or placement.room.slug,
        "camera_id": str(placement.id),
    }


@router.websocket("/ws/cv/{camera_id}")
async def ws_cv(camera_id: str, websocket: WebSocket) -> None:
    """Stream CV detections for a specific camera from Redis pub/sub."""
    await websocket.accept()
    try:
        # Resolve camera_id (UUID) → MQTT room slug so we can subscribe to the
        # correct Redis channel (cv:detections:{slug}) that the MQTT subscriber
        # writes. The slug is the room's stable MQTT identity and must match the
        # CV service's ROOM env var (see hub.backend.slug).
        # Fallback chain: config["mqtt_room"] override → room.slug → camera UUID.
        channel = f"cv:detections:{camera_id}"  # fallback
        try:
            uid = uuid.UUID(camera_id)
            async with AsyncSessionLocal() as session:
                res = await session.execute(
                    select(DevicePlacement)
                    .options(selectinload(DevicePlacement.room))
                    .where(DevicePlacement.id == uid, DevicePlacement.kind == "camera")
                    .limit(1)
                )
                placement = res.scalar_one_or_none()
                if placement:
                    mqtt_room: str | None = (placement.config or {}).get("mqtt_room")
                    if mqtt_room:
                        channel = f"cv:detections:{mqtt_room}"
                    elif placement.room:
                        channel = f"cv:detections:{placement.room.slug}"
        except Exception:
            pass  # keep fallback channel

        redis = websocket.app.state.redis
        pubsub = redis.pubsub()
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
