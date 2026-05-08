from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from hub.backend.config import settings
from hub.backend.db import get_session

router = APIRouter(prefix="/health", tags=["health"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


async def _check_postgres(session: AsyncSession) -> str:
    try:
        await session.execute(text("SELECT 1"))
        return "ok"
    except Exception:
        return "fail"


async def _check_redis(request: Request) -> str:
    try:
        await request.app.state.redis.ping()
        return "ok"
    except Exception:
        return "fail"


async def _check_mqtt() -> str:
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(settings.mqtt_host, settings.mqtt_port),
            timeout=2.0,
        )
        writer.close()
        await writer.wait_closed()
        return "ok"
    except Exception:
        return "fail"


@router.get("/ready")
async def readiness(
    request: Request,
    session: SessionDep,
    response: Response,
) -> dict[str, Any]:
    pg, redis, mqtt = await asyncio.gather(
        _check_postgres(session),
        _check_redis(request),
        _check_mqtt(),
    )
    checks = {"postgres": pg, "redis": redis, "mqtt": mqtt}
    all_ok = all(v == "ok" for v in checks.values())
    if not all_ok:
        response.status_code = 503
    return {"status": "ok" if all_ok else "degraded", "checks": checks}
