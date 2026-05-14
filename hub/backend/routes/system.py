"""System health metrics, logs, incidents, live WebSocket."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.backend.db import get_session
from hub.backend.models import AgentAudit

router = APIRouter(prefix="/api/system", tags=["system"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _f(s: str | None) -> float | None:
    return float(s) if s else None


def _i(s: str | None) -> int | None:
    return int(float(s)) if s else None


async def _collect_health(redis: Any) -> dict[str, Any]:
    keys = [
        "system:cpu_pct",
        "system:ram_used_gb",
        "system:ram_total_gb",
        "system:nvme_free_gb",
        "system:npu_pct",
        "system:temp_c",
        "models:cv_version",
        "models:llm_version",
        "models:whisper_version",
        "metrics:cv_p50_ms",
        "metrics:cv_p95_ms",
        "metrics:voice_e2e_p50_ms",
        "sync:last_bridge_ts",
    ]
    values = await redis.mget(*keys)
    (
        cpu_pct,
        ram_used,
        ram_total,
        nvme_free,
        npu_pct,
        temp_c,
        cv_ver,
        llm_ver,
        whisper_ver,
        cv_p50,
        cv_p95,
        voice_p50,
        last_bridge,
    ) = values

    svc_names = ["cv", "voice", "agent", "mqtt", "postgres", "redis"]
    heartbeats = await redis.mget(*[f"heartbeat:{s}" for s in svc_names])
    services = [
        {"name": name, "status": "ok" if hb else "offline", "uptime": hb}
        for name, hb in zip(svc_names, heartbeats, strict=True)
    ]
    t1_queue_depth = await redis.llen("sync:t1_queue")

    return {
        "services": services,
        "hardware": {
            "cpu_pct": _f(cpu_pct) or 0.0,
            "ram_used_gb": _f(ram_used) or 0.0,
            "ram_total_gb": _f(ram_total) or 8.0,
            "nvme_free_gb": _f(nvme_free) or 0.0,
            "npu_pct": _f(npu_pct),
            "temp_c": _f(temp_c),
        },
        "latency": {
            "cv_p50_ms": _i(cv_p50),
            "cv_p95_ms": _i(cv_p95),
            "voice_e2e_p50_ms": _i(voice_p50),
        },
        "models": {
            "cv_version": cv_ver,
            "llm_version": llm_ver,
            "whisper_version": whisper_ver,
        },
        "sync": {
            "last_bridge_ts": last_bridge,
            "t1_queue_depth": t1_queue_depth or 0,
        },
    }


@router.get("/health")
async def system_health(request: Request) -> dict[str, Any]:
    return await _collect_health(request.app.state.redis)


@router.get("/logs/{service}")
async def get_logs(
    service: str,
    request: Request,
    tail: int = Query(200, le=500),
) -> list[str]:
    """Last N log lines from Redis ring buffer (edge services push via LPUSH+LTRIM)."""
    redis = request.app.state.redis
    lines = await redis.lrange(f"logs:{service}", 0, tail - 1)
    if not lines:
        return [f"[Немає логів для '{service}' — edge-сервіс ще не писав у Redis]"]
    return list(reversed(lines))


@router.get("/incidents")
async def get_incidents(session: SessionDep) -> list[dict[str, Any]]:
    """Recent DENY decisions and prompt-injection attempts from AgentAudit (last 7 days)."""
    cutoff = datetime.now(UTC) - timedelta(days=7)
    res = await session.execute(
        select(AgentAudit)
        .where(AgentAudit.action_class == "DENY", AgentAudit.timestamp >= cutoff)
        .order_by(AgentAudit.timestamp.desc())
        .limit(100)
    )
    rows = list(res.scalars())
    return [
        {
            "id": str(r.id),
            "timestamp": r.timestamp.isoformat(),
            "intent_text": r.intent_text,
            "tool": r.tool,
            "severity": (
                "high"
                if any(
                    kw in r.intent_text.lower()
                    for kw in ("inject", "ignore", "disregard", "system prompt")
                )
                else "medium"
            ),
        }
        for r in rows
    ]


@router.websocket("/ws")
async def system_ws(websocket: WebSocket) -> None:
    """Push health snapshot every 5 s."""
    await websocket.accept()
    redis = websocket.app.state.redis
    try:
        while True:
            snapshot = await _collect_health(redis)
            await websocket.send_json(snapshot)
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
