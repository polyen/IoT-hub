"""Security mode — arm/disarm via policy + Redis state."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.backend.db import get_session
from hub.backend.models import ConfirmRequest, Event
from hub.backend.schemas.confirm import ConfirmRequestOut
from hub.backend.services.policy_loader import load_policy, simulate

router = APIRouter(prefix="/api/security", tags=["security"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]

_MODE_KEY = "home:security:mode"
_SINCE_KEY = "home:security:since"
_MODE_MAP = {"arm_home": "armed_home", "arm_away": "armed_away", "disarm": "disarmed"}

_SECURITY_EVENT_TYPES = [
    "alarm",
    "person_detected",
    "motion",
    "fire",
    "smoke",
    "fall_detected",
]


class SecurityState(BaseModel):
    mode: str  # disarmed | armed_home | armed_away
    since: str | None = None


class SecurityCommand(BaseModel):
    action: str  # arm_home | arm_away | disarm


@router.get("/state", response_model=SecurityState)
async def get_state(request: Request) -> SecurityState:
    redis = request.app.state.redis
    mode = (await redis.get(_MODE_KEY)) or "disarmed"
    since = await redis.get(_SINCE_KEY)
    return SecurityState(mode=mode, since=since)


@router.post("/command")
async def security_command(
    body: SecurityCommand,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    if body.action not in _MODE_MAP:
        raise HTTPException(
            status_code=422,
            detail=f"action must be one of {list(_MODE_MAP)}",
        )

    sim = simulate(
        intent_text=f"Security: {body.action}",
        tool="mqtt_publish",
        payload={"topic": "home/security/cmd", "payload": {"action": body.action}},
    )
    redis = request.app.state.redis
    topic = "home/security/cmd"

    if sim["class"] == "DENY":
        raise HTTPException(
            status_code=403,
            detail=f"Заблоковано політикою: {sim.get('reason', '')}",
        )

    if sim["class"] == "CONFIRM":
        policy = load_policy()
        timeout = policy.get("confirmation", {}).get("default_timeout_sec", 60)
        req = ConfirmRequest(
            tool="mqtt_publish",
            payload={"topic": topic, "payload": {"action": body.action}},
            intent_text=f"Змінити режим охорони: {body.action}",
            confirm_message=f"Змінити стан безпеки: {body.action}?",
            expires_at=datetime.now(UTC) + timedelta(seconds=timeout),
            state="pending",
        )
        session.add(req)
        await session.commit()
        await redis.publish(
            "confirm:request",
            ConfirmRequestOut.model_validate(req).model_dump_json(),
        )
        return {"result": "confirm_required", "confirm_id": str(req.id)}

    # AUTO
    await redis.publish(f"mqtt:publish:{topic}", json.dumps({"action": body.action}))
    await redis.set(_MODE_KEY, _MODE_MAP[body.action])
    await redis.set(_SINCE_KEY, datetime.now(UTC).isoformat())
    return {"result": "executed"}


@router.get("/events")
async def get_events(session: SessionDep, limit: int = 20) -> list[dict[str, Any]]:
    res = await session.execute(
        select(Event)
        .where(Event.type.in_(_SECURITY_EVENT_TYPES))
        .order_by(Event.timestamp.desc())
        .limit(limit)
    )
    return [
        {
            "id": str(e.id),
            "timestamp": e.timestamp.isoformat(),
            "type": e.type,
            "room": e.room,
            "payload": e.payload,
        }
        for e in res.scalars()
    ]
