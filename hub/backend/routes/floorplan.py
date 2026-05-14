"""Floor plan CRUD — GET returns full snapshot; PUT does atomic replace."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.backend.db import get_session
from hub.backend.models import DevicePlacement, FloorPlan, Room
from hub.backend.schemas.floorplan import (
    DevicePlacementOut,
    DiscoveredDevice,
    FloorPlanDataOut,
    FloorPlanIn,
    FloorPlanOut,
    RoomOut,
)

router = APIRouter(prefix="/api/floorplan", tags=["floorplan"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]

DEFAULT_ROOMS: list[dict[str, Any]] = [
    {
        "name": "Вітальня",
        "type": "living",
        "polygon": [[0.05, 0.05], [0.55, 0.05], [0.55, 0.55], [0.05, 0.55]],
        "order": 0,
    },
    {
        "name": "Кухня",
        "type": "kitchen",
        "polygon": [[0.6, 0.05], [0.95, 0.05], [0.95, 0.55], [0.6, 0.55]],
        "order": 1,
    },
    {
        "name": "Спальня",
        "type": "bedroom",
        "polygon": [[0.05, 0.6], [0.55, 0.6], [0.55, 0.95], [0.05, 0.95]],
        "order": 2,
    },
    {
        "name": "Коридор",
        "type": "hall",
        "polygon": [[0.6, 0.6], [0.95, 0.6], [0.95, 0.95], [0.6, 0.95]],
        "order": 3,
    },
]


@router.get("", response_model=FloorPlanDataOut)
async def get_floorplan(session: SessionDep) -> FloorPlanDataOut:
    plans_res = await session.execute(select(FloorPlan).order_by(FloorPlan.created_at))
    plans = list(plans_res.scalars())

    if not plans:
        # Seed default plan
        plan = FloorPlan(name="Мій дім", floor=1, width=1.0, height=0.7)
        session.add(plan)
        await session.flush()
        for rd in DEFAULT_ROOMS:
            session.add(Room(floor_plan_id=plan.id, **rd))
        await session.commit()
        await session.refresh(plan)
        plans = [plan]

    rooms_res = await session.execute(
        select(Room).where(Room.floor_plan_id.in_([p.id for p in plans])).order_by(Room.order)
    )
    rooms = list(rooms_res.scalars())

    placements_res = await session.execute(
        select(DevicePlacement).where(DevicePlacement.room_id.in_([r.id for r in rooms]))
    )
    placements = list(placements_res.scalars())

    return FloorPlanDataOut(
        floor_plans=[FloorPlanOut.model_validate(p) for p in plans],
        rooms=[RoomOut.model_validate(r) for r in rooms],
        placements=[DevicePlacementOut.model_validate(p) for p in placements],
    )


@router.put("", response_model=FloorPlanDataOut)
async def put_floorplan(body: FloorPlanIn, session: SessionDep) -> FloorPlanDataOut:
    """Atomic replace: delete old plan + rooms + placements, insert new."""
    existing = (await session.execute(select(FloorPlan).limit(1))).scalar_one_or_none()
    if existing:
        # cascade delete will remove rooms & placements
        await session.delete(existing)
        await session.flush()

    plan = FloorPlan(
        name=body.name,
        floor=body.floor,
        width=body.width,
        height=body.height,
        background_url=body.background_url,
    )
    session.add(plan)
    await session.flush()

    room_id_map: dict[str, uuid.UUID] = {}
    for ri in body.rooms:
        room = Room(
            id=ri.id or uuid.uuid4(),
            floor_plan_id=plan.id,
            name=ri.name,
            type=ri.type,
            polygon=ri.polygon,
            color=ri.color,
            order=ri.order,
        )
        session.add(room)
        room_id_map[str(ri.id)] = room.id

    await session.flush()

    for pi in body.placements:
        real_room_id = room_id_map.get(str(pi.room_id), pi.room_id)
        session.add(
            DevicePlacement(
                id=pi.id or uuid.uuid4(),
                room_id=real_room_id,
                device_id=pi.device_id,
                kind=pi.kind,
                x=pi.x,
                y=pi.y,
                label=pi.label,
                config=pi.config,
            )
        )

    await session.commit()
    return await get_floorplan(session)


@router.get("/room_states")
async def room_states(request: Request, session: SessionDep) -> dict[str, list[str]]:
    """Return room IDs that have presence or alert based on Redis device states."""
    redis = request.app.state.redis
    res = await session.execute(select(DevicePlacement))
    placements = list(res.scalars())

    presence_rooms: set[str] = set()
    alert_rooms: set[str] = set()

    for p in placements:
        state = await redis.hgetall(f"home:state:{p.device_id}")
        if not state:
            continue
        if (
            state.get("presence") == "true"
            or state.get("motion") == "true"
            or state.get("occupied") == "true"
        ):
            presence_rooms.add(str(p.room_id))
        if (
            state.get("alert") == "true"
            or state.get("alarm") == "true"
            or state.get("fire") == "true"
        ):
            alert_rooms.add(str(p.room_id))

    return {"presence_rooms": list(presence_rooms), "alert_rooms": list(alert_rooms)}


@router.get("/devices/discovered", response_model=list[DiscoveredDevice])
async def discovered_devices(session: SessionDep) -> list[DiscoveredDevice]:
    """Return devices known from existing placements (redis/mqtt discovery in future)."""
    res = await session.execute(select(DevicePlacement))
    placements = list(res.scalars())
    seen: dict[str, DiscoveredDevice] = {}
    for p in placements:
        if p.device_id not in seen:
            seen[p.device_id] = DiscoveredDevice(
                device_id=p.device_id,
                kind_guess=p.kind,
                last_seen=None,
                source="redis",
            )
    return list(seen.values())
