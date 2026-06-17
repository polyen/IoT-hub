"""Device state read and command dispatch with policy enforcement.

Structured-detail contract for command failures
------------------------------------------------
Failed commands raise HTTPException with a dict ``detail``:
  {
    "failure_kind": str,          # "device_not_found" | "denied"
    "message":      str,          # Ukrainian natural-language explanation
    "cta":          dict | None,  # {"label": str, "to": str} or None
  }
HTTP status codes are preserved (404 / 403).  CONFIRM and AUTO paths are
unchanged and always return a ``CommandResult`` JSON body.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.backend.db import get_session
from hub.backend.models import ConfirmRequest, DevicePlacement, Room
from hub.backend.services.policy_loader import simulate

router = APIRouter(prefix="/api/devices", tags=["devices"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class CommandBody(BaseModel):
    payload: dict[str, Any]
    intent_text: str = "UI command"


class CommandResult(BaseModel):
    result: str  # "auto_executed" | "confirm_required" | "denied"
    confirm_id: str | None = None


class DeviceRow(BaseModel):
    """Full device registry row, returned by GET /api/devices."""

    id: uuid.UUID
    device_id: str
    kind: str
    label: str | None
    room_id: uuid.UUID
    room_name: str
    room_slug: str
    room_aliases: list[str]
    aliases: list[str]
    controllable: bool
    actions: list[str]
    config: dict[str, Any]

    model_config = {"from_attributes": True}


class DeviceUpdate(BaseModel):
    """All fields optional — PATCH semantics."""

    label: str | None = None
    aliases: list[str] | None = None
    controllable: bool | None = None
    actions: list[str] | None = None
    config: dict[str, Any] | None = None


async def _publish_registry_changed(request: Request) -> None:
    """Notify all subscribers that the device registry has changed."""
    try:
        await request.app.state.redis.publish("devices:registry_changed", "")
    except Exception:
        pass


@router.get("", response_model=list[DeviceRow])
async def list_devices(session: SessionDep, request: Request) -> list[DeviceRow]:
    """List all DevicePlacements joined with their Room info."""
    result = await session.execute(
        select(DevicePlacement, Room).join(Room, DevicePlacement.room_id == Room.id)
    )
    rows = result.all()
    return [
        DeviceRow(
            id=p.id,
            device_id=p.device_id,
            kind=p.kind,
            label=p.label,
            room_id=r.id,
            room_name=r.name,
            room_slug=r.slug,
            room_aliases=list(r.aliases or []),
            aliases=list(p.aliases or []),
            controllable=p.controllable,
            actions=list(p.actions or []),
            config=dict(p.config or {}),
        )
        for p, r in rows
    ]


@router.patch("/{device_id}", response_model=DeviceRow)
async def patch_device(
    device_id: str, body: DeviceUpdate, session: SessionDep, request: Request
) -> DeviceRow:
    """Partial update of a DevicePlacement (aliases, controllable, actions, config, label)."""
    res = await session.execute(
        select(DevicePlacement, Room)
        .join(Room, DevicePlacement.room_id == Room.id)
        .where(DevicePlacement.device_id == device_id)
        .limit(1)
    )
    row = res.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Device not found")

    placement, room = row

    if body.label is not None:
        placement.label = body.label
    if body.aliases is not None:
        placement.aliases = body.aliases
    if body.controllable is not None:
        placement.controllable = body.controllable
    if body.actions is not None:
        placement.actions = body.actions
    if body.config is not None:
        # Merge: keep existing keys, update/add new ones
        merged = dict(placement.config or {})
        merged.update(body.config)
        placement.config = merged

    await session.commit()
    await session.refresh(placement)
    await _publish_registry_changed(request)

    return DeviceRow(
        id=placement.id,
        device_id=placement.device_id,
        kind=placement.kind,
        label=placement.label,
        room_id=room.id,
        room_name=room.name,
        room_slug=room.slug,
        room_aliases=list(room.aliases or []),
        aliases=list(placement.aliases or []),
        controllable=placement.controllable,
        actions=list(placement.actions or []),
        config=dict(placement.config or {}),
    )


@router.get("/{device_id}/state")
async def device_state(device_id: str) -> dict[str, Any]:
    """Proxy to Redis home:state:{device_id}."""
    from hub.backend.main import app  # noqa: PLC0415

    redis = app.state.redis
    data = await redis.hgetall(f"home:state:{device_id}")
    return data or {}


@router.post("/{device_id}/command", response_model=CommandResult)
async def device_command(device_id: str, body: CommandBody, session: SessionDep) -> CommandResult:
    res = await session.execute(
        select(DevicePlacement).where(DevicePlacement.device_id == device_id).limit(1)
    )
    placement = res.scalar_one_or_none()
    if not placement:
        raise HTTPException(
            status_code=404,
            detail={
                "failure_kind": "device_not_found",
                "message": "Пристрій не зареєстровано у плані. Додайте його на сторінці «Пристрої».",
                "cta": {"label": "Пристрої", "to": "/more/devices"},
            },
        )

    cfg: dict[str, object] = placement.config or {}
    topic = cfg.get("mqtt_topic", f"home/{device_id}/cmd")

    sim = simulate(
        intent_text=body.intent_text,
        tool="mqtt_publish",
        payload={"topic": topic, "payload": body.payload},
    )

    if sim["class"] == "DENY":
        raise HTTPException(
            status_code=403,
            detail={
                "failure_kind": "denied",
                "message": "Цю дію заборонено політикою безпеки.",
                "cta": None,
            },
        )

    if sim["class"] == "CONFIRM":
        from datetime import UTC, datetime, timedelta  # noqa: PLC0415

        from hub.backend.services.policy_loader import load_policy  # noqa: PLC0415

        policy = load_policy()
        timeout = policy.get("confirmation", {}).get("default_timeout_sec", 60)
        req = ConfirmRequest(
            tool="mqtt_publish",
            payload={"topic": topic, "payload": body.payload},
            intent_text=body.intent_text,
            confirm_message=f"Виконати команду для {placement.label or device_id}?",
            expires_at=datetime.now(UTC) + timedelta(seconds=timeout),
            state="pending",
        )
        session.add(req)
        await session.commit()

        # Publish to WebSocket stream
        try:
            from hub.backend.main import app  # noqa: PLC0415
            from hub.backend.schemas.confirm import ConfirmRequestOut  # noqa: PLC0415

            await app.state.redis.publish(
                "confirm:request",
                ConfirmRequestOut.model_validate(req).model_dump_json(),
            )
        except Exception:
            pass

        return CommandResult(result="confirm_required", confirm_id=str(req.id))

    # AUTO — publish MQTT
    try:
        from hub.backend.main import app  # noqa: PLC0415

        await app.state.redis.publish(
            f"mqtt:publish:{topic}",
            json.dumps(body.payload),
        )
    except Exception:
        pass

    return CommandResult(result="auto_executed")
