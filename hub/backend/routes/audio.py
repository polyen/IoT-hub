"""Audio device registry and active input/output configuration."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select

from hub.backend.db import AsyncSessionLocal
from hub.backend.models import DevicePlacement
from hub.backend.schemas.audio import AudioConfig, AudioDevice

router = APIRouter(tags=["audio"])

_INPUT_KEY = "audio:input_device"
_OUTPUT_KEY = "audio:output_device"
_CHANGE_CHANNEL = "audio:config_changed"


@router.get("/api/audio/devices", response_model=list[AudioDevice])
async def list_audio_devices() -> list[AudioDevice]:
    """Return all available audio input and output devices.

    RTSP sources come from camera DevicePlacements in the DB.
    A default local mic/speaker entry is always included so the voice
    container's sounddevice is selectable even without cameras.
    """
    devices: list[AudioDevice] = [
        AudioDevice(id="local:mic:0", name="RPi мікрофон", type="local_mic", available=True),
        AudioDevice(id="local:spk:0", name="RPi колонка", type="local_speaker", available=True),
    ]

    async with AsyncSessionLocal() as session:
        res = await session.execute(select(DevicePlacement).where(DevicePlacement.kind == "camera"))
        for p in res.scalars():
            cfg: dict[str, Any] = p.config or {}
            rtsp_url: str | None = cfg.get("rtsp_url")
            name = p.label or p.device_id
            devices.append(
                AudioDevice(
                    id=f"camera:mic:{p.id}",
                    name=f"{name} (мікрофон)",
                    type="rtsp_mic",
                    available=bool(rtsp_url),
                )
            )
            devices.append(
                AudioDevice(
                    id=f"camera:spk:{p.id}",
                    name=f"{name} (динамік)",
                    type="rtsp_speaker",
                    available=bool(rtsp_url),
                )
            )

    return devices


@router.get("/api/audio/config", response_model=AudioConfig)
async def get_audio_config(request: Request) -> AudioConfig:
    redis = request.app.state.redis
    input_id = await redis.get(_INPUT_KEY)
    output_id = await redis.get(_OUTPUT_KEY)
    return AudioConfig(
        input_id=input_id.decode() if isinstance(input_id, bytes) else input_id,
        output_id=output_id.decode() if isinstance(output_id, bytes) else output_id,
    )


@router.put("/api/audio/config", response_model=AudioConfig)
async def set_audio_config(body: AudioConfig, request: Request) -> AudioConfig:
    redis = request.app.state.redis
    if body.input_id is not None:
        await redis.set(_INPUT_KEY, body.input_id)
    if body.output_id is not None:
        await redis.set(_OUTPUT_KEY, body.output_id)
    # Notify voice pipeline of the change via pub/sub.
    await redis.publish(
        _CHANGE_CHANNEL,
        json.dumps(
            {
                "input_id": body.input_id,
                "output_id": body.output_id,
            }
        ),
    )
    return await get_audio_config(request)
