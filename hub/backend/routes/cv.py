"""Camera registry, snapshot, and CV detection WebSocket."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import time as _time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from hub.backend.config import settings
from hub.backend.db import AsyncSessionLocal, get_session
from hub.backend.models import DevicePlacement, Event
from hub.backend.schemas.cv import AnnotationRequest, CameraOut

logger = logging.getLogger(__name__)
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


async def _patch_mediamtx_path(client: httpx.AsyncClient, path: str, source: str) -> None:
    try:
        r = await client.patch(
            f"{settings.mediamtx_api}/v3/config/paths/patch/{path}",
            json={"source": source},
        )
        if r.is_success:
            logger.info("mediamtx: path %r → %r", path, source)
        else:
            logger.warning("mediamtx: PATCH %r returned %d", path, r.status_code)
    except Exception as exc:
        logger.warning("mediamtx: sync failed for path %r: %s", path, exc)


async def sync_camera_paths_to_mediamtx(cameras: list[tuple[str, dict[str, Any]]]) -> None:
    """Push camera stream URLs to mediamtx via its config API.

    Configures both the low-res sub-stream path (used by CV pipeline) and the
    optional HD path (used by frontend HLS/WebRTC player). Called on backend
    startup and after floor-plan saves — camera URLs are stored in
    DevicePlacement.config, not in env vars.

    cameras: list of (device_id, config_dict) from DevicePlacement rows.
    """
    async with httpx.AsyncClient(timeout=3.0) as client:
        for device_id, cfg in cameras:
            mediamtx_path = str(cfg.get("mediamtx_path") or device_id)

            rtsp_url = str(cfg.get("rtsp_url", "")).strip()
            if rtsp_url:
                await _patch_mediamtx_path(client, mediamtx_path, rtsp_url)

            rtsp_hd_url = str(cfg.get("rtsp_hd_url", "")).strip()
            if rtsp_hd_url:
                hd_path = str(cfg.get("mediamtx_hd_path") or f"{mediamtx_path}_hd")
                await _patch_mediamtx_path(client, hd_path, rtsp_hd_url)


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
        # If rtsp_hd_url or an explicit mediamtx_hd_path is set, route the frontend
        # HLS/WebRTC player to the high-res path while CV keeps the low-res one.
        if cfg.get("rtsp_hd_url") or cfg.get("mediamtx_hd_path"):
            hd_path = cfg.get("mediamtx_hd_path") or f"{mediamtx_path}_hd"
            stream_hls = cfg.get("stream_hls") or f"/hls/{hd_path}/index.m3u8"
        else:
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
    """Return snapshot info for a camera: thumbnail URL + latest event ID for feedback."""
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
    mediamtx_path = cfg.get("mediamtx_path") or placement.device_id

    # Thumbnail: MediaMTX ≥1.9 serves JPEG snapshots at /hls/{path}/thumb.jpg
    # via the web server (port 8888 in default config, proxied here via /hls/).
    thumbnail_url: str | None = f"/hls/{mediamtx_path}/thumb.jpg"

    # Latest camera/event for this camera's room — used as alert_id in feedback
    # so mining JOIN resolves correctly.
    latest_event_id: str | None = None
    room_slug: str | None = None
    try:
        from hub.backend.models import Room  # noqa: PLC0415

        # Resolve room slug from placement
        room_res = await session.execute(select(Room).where(Room.id == placement.room_id).limit(1))
        room_obj = room_res.scalar_one_or_none()
        if room_obj:
            room_slug = room_obj.slug
            ev_res = await session.execute(
                select(Event)
                .where(Event.room == room_slug, Event.type == "camera/event")
                .order_by(Event.timestamp.desc())
                .limit(1)
            )
            ev = ev_res.scalar_one_or_none()
            if ev:
                latest_event_id = str(ev.id)
    except Exception:
        pass

    return {
        "camera_id": camera_id,
        "frame_url": thumbnail_url,
        "event_id": latest_event_id,
    }


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


@router.post("/api/cv/annotate")
async def save_annotation(req: AnnotationRequest) -> dict[str, Any]:
    """Save a manually-annotated frame to the fire_smoke_mixed training dataset.

    Writes a JPEG image + YOLO-format label file (.txt) to the configured
    dataset directory.  class_id mapping: 0=person, 1=fire, 2=smoke.
    """
    try:
        image_bytes = base64.b64decode(req.image_b64)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid base64 image") from exc

    dataset_dir = Path(settings.annotation_dataset_dir)
    images_dir = dataset_dir / "images" / "train"
    labels_dir = dataset_dir / "labels" / "train"
    try:
        images_dir.mkdir(parents=True, exist_ok=True)
        labels_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Cannot create dataset dirs: {exc}") from exc

    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    stem = f"annot_{ts}"
    image_path = images_dir / f"{stem}.jpg"
    label_path = labels_dir / f"{stem}.txt"

    image_path.write_bytes(image_bytes)
    label_lines = [f"{b.class_id} {b.cx:.6f} {b.cy:.6f} {b.w:.6f} {b.h:.6f}" for b in req.boxes]
    label_path.write_text("\n".join(label_lines) + ("\n" if label_lines else ""))

    logger.info("Saved annotation %s (%d boxes)", stem, len(req.boxes))
    return {"saved": stem, "boxes": len(req.boxes)}


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
            ping_at = _time.monotonic() + 25
            while True:
                # get_message(timeout=1.0) blocks inside redis-py for up to 1s
                # then returns None — safe, no coroutine cancellation.
                # asyncio.wait_for is intentionally avoided: cancelling the
                # underlying read_response coroutine corrupts the pubsub reader
                # state and causes 30–60 s delivery stalls.
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg["type"] == "message":
                    await websocket.send_text(msg["data"])
                now = _time.monotonic()
                if now >= ping_at:
                    await websocket.send_text('{"type":"ping"}')
                    ping_at = now + 25
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
    except WebSocketDisconnect:
        pass
