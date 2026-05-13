"""Device state read and command dispatch with policy enforcement."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.backend.db import get_session
from hub.backend.models import ConfirmRequest, DevicePlacement
from hub.backend.services.policy_loader import simulate

router = APIRouter(prefix="/api/devices", tags=["devices"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class CommandBody(BaseModel):
    payload: dict[str, Any]
    intent_text: str = "UI command"


class CommandResult(BaseModel):
    result: str  # "auto_executed" | "confirm_required" | "denied"
    confirm_id: str | None = None


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
        raise HTTPException(status_code=404, detail="Device not found in floor plan")

    cfg: dict = placement.config or {}
    topic = cfg.get("mqtt_topic", f"home/{device_id}/cmd")

    sim = simulate(
        intent_text=body.intent_text,
        tool="mqtt_publish",
        payload={"topic": topic, "payload": body.payload},
    )

    if sim["class"] == "DENY":
        raise HTTPException(status_code=403, detail=f"Action denied by policy: {sim['reason']}")

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
