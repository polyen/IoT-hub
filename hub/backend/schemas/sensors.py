"""Pydantic schemas for the microclimate sensor endpoints."""

from __future__ import annotations

from pydantic import BaseModel


class RoomClimate(BaseModel):
    """Latest cached numeric readings for one room (from ``home:climate:{room}``)."""

    room: str
    ts: str | None = None
    # Arbitrary numeric fields (temperature, humidity, illuminance, power_w, …).
    values: dict[str, float]


class LatestClimateOut(BaseModel):
    """Map of room slug → latest climate snapshot."""

    rooms: dict[str, RoomClimate]


class TimeseriesPoint(BaseModel):
    """One time bucket; ``values`` holds the per-field average over the bucket."""

    t: str
    values: dict[str, float]


class TimeseriesOut(BaseModel):
    room: str
    bucket: str
    fields: list[str]
    points: list[TimeseriesPoint]
