"""Daily/weekly digest — event counts + LLM narrative."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.backend.db import get_session
from hub.backend.models import Event

router = APIRouter(prefix="/api/digest", tags=["digest"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]

_PERIODS: dict[str, timedelta] = {
    "today": timedelta(days=1),
    "yesterday": timedelta(days=2),
    "week": timedelta(days=7),
}

_YESTERDAY_END: dict[str, timedelta] = {
    "yesterday": timedelta(days=1),
}


def _period_window(period: str) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    if period == "yesterday":
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=1)
    elif period == "week":
        start = now - timedelta(days=7)
        end = now
    else:  # today
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now
    return start, end


@router.get("")
async def get_digest(
    session: SessionDep,
    request: Request,
    period: str = Query("today", pattern="^(today|yesterday|week)$"),
) -> dict[str, Any]:
    """Event counts per type + cached narrative from Redis."""
    start, end = _period_window(period)

    counts_res = await session.execute(
        select(Event.type, func.count(Event.id).label("n"))
        .where(Event.timestamp >= start, Event.timestamp < end)
        .group_by(Event.type)
        .order_by(func.count(Event.id).desc())
    )
    counts = {row.type: row.n for row in counts_res}

    total = sum(counts.values())

    # Per-hour counts (0-23) and peak hour
    hourly_res = await session.execute(
        select(
            func.extract("hour", Event.timestamp).label("hour"),
            func.count(Event.id).label("n"),
        )
        .where(Event.timestamp >= start, Event.timestamp < end)
        .group_by(func.extract("hour", Event.timestamp))
    )
    hourly_counts: dict[int, int] = {int(row.hour): row.n for row in hourly_res}
    peak_hour: int | None = (
        max(hourly_counts, key=lambda h: hourly_counts[h]) if hourly_counts else None
    )

    # Narrative: try Redis cache first (edge agent writes digest:narrative:{period})
    redis = request.app.state.redis
    narrative: str | None = await redis.get(f"digest:narrative:{period}")

    return {
        "period": period,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "total_events": total,
        "counts": counts,
        "peak_hour": peak_hour,
        "hourly_counts": hourly_counts,
        "narrative": narrative,
    }


@router.get("/summary")
async def get_digest_summary(session: SessionDep) -> dict[str, Any]:
    """Compact today-only stats for the Home page InsightsStrip."""
    start, end = _period_window("today")

    counts_res = await session.execute(
        select(Event.type, func.count(Event.id).label("n"))
        .where(Event.timestamp >= start, Event.timestamp < end)
        .group_by(Event.type)
    )
    counts = {row.type: row.n for row in counts_res}

    total = sum(counts.values())
    alerts = sum(v for k, v in counts.items() if k in ("alert", "fire", "smoke", "fall"))
    faces = counts.get("camera/identity", 0)

    # Cameras online: distinct rooms that published a camera/event today
    cameras_res = await session.execute(
        select(func.count(func.distinct(Event.room))).where(
            Event.timestamp >= start,
            Event.timestamp < end,
            Event.type == "camera/event",
            Event.room.isnot(None),
        )
    )
    cameras_online: int = cameras_res.scalar_one_or_none() or 0

    return {
        "total_events": total,
        "alerts_today": alerts,
        "faces_today": faces,
        "cameras_online": cameras_online,
    }


@router.get("/narrative")
async def get_narrative(
    request: Request,
    period: str = Query("today", pattern="^(today|yesterday|week)$"),
    local_only: bool = Query(True),
) -> dict[str, str | None]:
    """Return cached narrative or trigger generation via Redis task queue."""
    redis = request.app.state.redis
    narrative: str | None = await redis.get(f"digest:narrative:{period}")

    if not narrative:
        # Signal edge agent to generate; it writes back to digest:narrative:{period}
        await redis.xadd(
            "agent:tasks",
            {"task": "summarize_period", "period": period, "local_only": str(local_only)},
            maxlen=50,
        )
        narrative = None

    return {"period": period, "narrative": narrative, "local_only": str(local_only)}
