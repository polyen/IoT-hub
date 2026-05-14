"""Privacy report, cloud consent toggle, DSAR wipe (CONFIRM-flow)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from hub.backend.db import get_session
from hub.backend.models import ConfirmRequest

router = APIRouter(prefix="/api/privacy", tags=["privacy"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]

_CONSENT_KEY = "privacy:cloud_consent"
_BYTES_PREFIX = "privacy:bytes_cloud:"  # keys: privacy:bytes_cloud:{tier}


@router.get("/report")
async def privacy_report(request: Request) -> dict[str, Any]:
    """Cloud bytes sent (7d), by tier, cloud consent state."""
    redis = request.app.state.redis

    tiers = [0, 1, 2, 3]
    tier_keys = [f"{_BYTES_PREFIX}{t}" for t in tiers]
    values = await redis.mget(*tier_keys)
    by_tier = {str(t): int(v or 0) for t, v in zip(tiers, values, strict=True)}
    total_bytes = sum(by_tier.values())

    consent_raw = await redis.get(_CONSENT_KEY)
    cloud_consent = consent_raw != "false" if consent_raw else True

    # Tool breakdown (optional — edge bridge writes privacy:tool:{tool_name})
    tool_keys = await redis.keys("privacy:tool:*")
    tool_values = await redis.mget(*tool_keys) if tool_keys else []
    by_tool = {
        k.split(":")[-1]: int(v or 0) for k, v in zip(tool_keys, tool_values, strict=True) if v
    }

    return {
        "sent_to_cloud_bytes_7d": total_bytes,
        "by_tier": by_tier,
        "by_tool": by_tool,
        "cloud_consent_state": cloud_consent,
        "report_generated_at": datetime.now(UTC).isoformat(),
    }


class ConsentBody(BaseModel):
    enabled: bool


@router.post("/cloud_consent")
async def set_cloud_consent(body: ConsentBody, request: Request) -> dict[str, bool]:
    """Toggle global cloud fallback consent."""
    redis = request.app.state.redis
    await redis.set(_CONSENT_KEY, "true" if body.enabled else "false")
    await redis.publish("privacy:consent_changed", "true" if body.enabled else "false")
    return {"cloud_consent_state": body.enabled}


class WipeBody(BaseModel):
    tiers: list[int]  # [2, 3]
    since: str  # ISO datetime
    until: str  # ISO datetime


@router.post("/wipe")
async def request_wipe(body: WipeBody, session: SessionDep) -> dict[str, str]:
    """DSAR-style wipe: creates a CONFIRM-flow request, does not execute immediately."""
    if not body.tiers or not all(0 <= t <= 3 for t in body.tiers):
        raise HTTPException(status_code=422, detail="tiers must be list of 0-3")

    tier_str = "+".join(f"T{t}" for t in sorted(set(body.tiers)))
    confirm = ConfirmRequest(
        id=uuid.uuid4(),
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(seconds=120),
        tool="privacy_wipe",
        payload={"tiers": body.tiers, "since": body.since, "until": body.until},
        intent_text=f"Видалити дані {tier_str} за {body.since[:10]}–{body.until[:10]}",
        confirm_message=f"Безповоротно видалити всі {tier_str} дані за вказаний період?",
        state="pending",
    )
    session.add(confirm)
    await session.commit()
    return {"confirm_id": str(confirm.id), "status": "awaiting_confirmation"}
