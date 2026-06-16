"""Microclimate sensor endpoints: live latest values + historical timeseries.

``/api/sensors/latest`` reads the per-room Redis cache that ``mqtt_subscriber``
maintains (``home:climate:{room}``) — cheap, real-time, no hypertable scan.

``/api/sensors/timeseries`` aggregates the ``events`` hypertable (``type='sensors'``)
with TimescaleDB ``time_bucket`` so the climate page can chart trends and
correlations. Field names are allowlisted by regex before being interpolated
into ``payload->>'<field>'`` to keep the dynamic SQL injection-safe.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from hub.backend.db import get_session
from hub.backend.mqtt_subscriber import _CLIMATE_KEY_PREFIX
from hub.backend.schemas.sensors import (
    LatestClimateOut,
    RoomClimate,
    TimeseriesOut,
    TimeseriesPoint,
)

router = APIRouter(prefix="/api/sensors", tags=["sensors"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]

# Only flat snake_case field names may reach the dynamic ``payload->>'…'``.
_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

# Range token → (lookback, time_bucket width, human label). Buckets are chosen so
# each range yields a manageable ~60–170 points regardless of how chatty the
# sensors are.  The width is a timedelta because asyncpg binds it as a Postgres
# ``interval`` — passing a string like ``'15 minutes'`` instead raises
# ``'str' object has no attribute 'days'`` when it tries to encode the parameter.
_RANGES: dict[str, tuple[timedelta, timedelta, str]] = {
    "1h": (timedelta(hours=1), timedelta(minutes=1), "1 minute"),
    "6h": (timedelta(hours=6), timedelta(minutes=5), "5 minutes"),
    "24h": (timedelta(hours=24), timedelta(minutes=15), "15 minutes"),
    "7d": (timedelta(days=7), timedelta(hours=1), "1 hour"),
}


@router.get("/latest", response_model=LatestClimateOut)
async def latest(request: Request) -> LatestClimateOut:
    """Latest cached numeric readings per room slug."""
    redis = request.app.state.redis
    rooms: dict[str, RoomClimate] = {}
    async for key in redis.scan_iter(match=f"{_CLIMATE_KEY_PREFIX}*"):
        slug = key[len(_CLIMATE_KEY_PREFIX) :]
        raw = await redis.hgetall(key)
        if not raw:
            continue
        ts = raw.pop("ts", None)
        values: dict[str, float] = {}
        for field_name, value in raw.items():
            try:
                values[field_name] = float(value)
            except (TypeError, ValueError):
                continue
        rooms[slug] = RoomClimate(room=slug, ts=ts, values=values)
    return LatestClimateOut(rooms=rooms)


@router.get("/timeseries", response_model=TimeseriesOut)
async def timeseries(
    session: SessionDep,
    room: str = Query(..., description="Room slug"),
    fields: str = Query(..., description="Comma-separated numeric field names"),
    range: str = Query("24h", description="One of 1h, 6h, 24h, 7d"),
) -> TimeseriesOut:
    """Bucketed averages of selected sensor fields for one room."""
    if range not in _RANGES:
        raise HTTPException(status_code=422, detail=f"range must be one of {list(_RANGES)}")
    lookback, bucket, bucket_label = _RANGES[range]

    field_list = [f.strip() for f in fields.split(",") if f.strip()]
    if not field_list:
        raise HTTPException(status_code=422, detail="fields is required")
    for f in field_list:
        if not _FIELD_RE.match(f):
            raise HTTPException(status_code=422, detail=f"invalid field name: {f!r}")
    # de-dup while preserving order
    field_list = list(dict.fromkeys(field_list))

    since = datetime.now(UTC) - lookback
    # Field names are regex-validated above, so this interpolation is safe; the
    # bucket width (a timedelta → interval), room and lower bound are bound params.
    # The ``jsonb_typeof = 'number'`` guard skips non-numeric values: several
    # sensors share the ``home/{room}/sensors`` topic, so a field that is text in
    # one message would otherwise abort the whole query with a cast error.
    aggs = ",\n        ".join(
        f"avg(CASE WHEN jsonb_typeof(payload->'{f}') = 'number' "
        f"THEN (payload->>'{f}')::double precision END) AS {f}"
        for f in field_list
    )
    stmt = text(f"""
        SELECT time_bucket(:bucket, timestamp) AS t,
        {aggs}
        FROM events
        WHERE type = 'sensors'
          AND tier <= 1
          AND room = :room
          AND timestamp >= :since
        GROUP BY t
        ORDER BY t ASC
        """).bindparams(bindparam("bucket"), bindparam("room"), bindparam("since"))

    result = await session.execute(stmt, {"bucket": bucket, "room": room, "since": since})

    points: list[TimeseriesPoint] = []
    for row in result.mappings():
        values = {f: float(row[f]) for f in field_list if row[f] is not None}
        if not values:
            continue
        points.append(TimeseriesPoint(t=row["t"].isoformat(), values=values))

    return TimeseriesOut(room=room, bucket=bucket_label, fields=field_list, points=points)
