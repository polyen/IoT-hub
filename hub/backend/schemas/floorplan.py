from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator


class RoomOut(BaseModel):
    id: uuid.UUID
    floor_plan_id: uuid.UUID
    name: str
    type: str
    polygon: list[list[float]]
    color: str | None
    order: int

    model_config = {"from_attributes": True}


class DevicePlacementOut(BaseModel):
    id: uuid.UUID
    room_id: uuid.UUID
    device_id: str
    kind: str
    x: float
    y: float
    label: str | None
    config: dict[str, Any]

    model_config = {"from_attributes": True}


class FloorPlanOut(BaseModel):
    id: uuid.UUID
    name: str
    floor: int
    width: float
    height: float
    background_url: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FloorPlanDataOut(BaseModel):
    floor_plans: list[FloorPlanOut]
    rooms: list[RoomOut]
    placements: list[DevicePlacementOut]


class RoomIn(BaseModel):
    id: uuid.UUID | None = None
    name: str
    type: str = "other"
    polygon: list[list[float]]
    color: str | None = None
    order: int = 0

    @field_validator("polygon")
    @classmethod
    def validate_polygon(cls, v: list[list[float]]) -> list[list[float]]:
        if len(v) < 3:
            raise ValueError("polygon must have at least 3 points")
        for pt in v:
            if len(pt) != 2:
                raise ValueError("each point must be [x, y]")
        return v


class DevicePlacementIn(BaseModel):
    id: uuid.UUID | None = None
    room_id: uuid.UUID
    device_id: str
    kind: str
    x: float = 0.5
    y: float = 0.5
    label: str | None = None
    config: dict[str, Any] = {}


class FloorPlanIn(BaseModel):
    name: str = "Мій дім"
    floor: int = 1
    width: float = 1.0
    height: float = 1.0
    background_url: str | None = None
    rooms: list[RoomIn] = []
    placements: list[DevicePlacementIn] = []


class DiscoveredDevice(BaseModel):
    device_id: str
    kind_guess: str
    last_seen: str | None
    source: str  # "mqtt" | "redis"
