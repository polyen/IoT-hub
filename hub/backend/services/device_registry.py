"""Cached, voice-side device lookup over DevicePlacement + Room.

Goals:
- Sub-millisecond resolve_device(kind, room_slug, alias) lookup.
- Hot reload via Redis pub/sub on 'devices:registry_changed' (published by
  routes/devices.py and routes/floorplan.py after any CRUD).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

# Sentinel: registry not yet loaded
_NOT_LOADED: list[Any] = []


@dataclass(frozen=True)
class ResolvedDevice:
    placement_id: uuid.UUID
    device_id: str
    kind: str
    label: str | None
    room_slug: str
    room_name_ua: str
    mqtt_command_topic: str
    mqtt_state_topic: str | None
    actions: list[str]
    payload_on: dict[str, Any] | None
    payload_off: dict[str, Any] | None
    # For alias matching: all UA strings recognised for this device
    device_aliases: list[str] = field(default_factory=list)
    # For alias matching: all UA strings recognised for this room
    room_aliases: list[str] = field(default_factory=list)


class DeviceRegistry:
    """In-process cache of controllable DevicePlacements with their room context.

    Thread-safety: all methods are async; concurrent reads share the same list
    reference (CPython GIL is sufficient for read); writes hold ``_lock``.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        redis_client: Any,
    ) -> None:
        self._session_factory = session_factory
        self._redis = redis_client
        self._devices: list[ResolvedDevice] = []
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Load / reload
    # ------------------------------------------------------------------

    async def load(self) -> None:
        """Full reload from DB.  Replaces in-memory cache atomically."""
        from hub.backend.models import DevicePlacement, Room  # noqa: PLC0415

        async with self._session_factory() as session:
            result = await session.execute(
                select(DevicePlacement, Room)
                .join(Room, DevicePlacement.room_id == Room.id)
                .where(DevicePlacement.controllable.is_(True))
            )
            rows = result.all()

        devices: list[ResolvedDevice] = []
        for placement, room in rows:
            cfg: dict[str, Any] = placement.config or {}
            slug: str = room.slug
            kind: str = placement.kind

            cmd_topic: str = cfg.get("mqtt_topic", f"home/{slug}/{kind}/cmd")
            state_topic: str | None = cfg.get("mqtt_state_topic") or None

            payload_on: dict[str, Any] | None = cfg.get("payload_on") or {"state": "on"}
            payload_off: dict[str, Any] | None = cfg.get("payload_off") or {"state": "off"}

            devices.append(
                ResolvedDevice(
                    placement_id=placement.id,
                    device_id=placement.device_id,
                    kind=kind,
                    label=placement.label,
                    room_slug=slug,
                    room_name_ua=room.name,
                    mqtt_command_topic=cmd_topic,
                    mqtt_state_topic=state_topic,
                    actions=list(placement.actions or []),
                    payload_on=payload_on,
                    payload_off=payload_off,
                    device_aliases=list(placement.aliases or []),
                    room_aliases=list(room.aliases or []),
                )
            )

        async with self._lock:
            self._devices = devices

        # Publish state-topic → device_id map so mqtt_subscriber can write
        # home:state:{device_id} when it receives device state messages.
        topic_map: dict[str, str] = {
            d.mqtt_state_topic: d.device_id for d in devices if d.mqtt_state_topic
        }
        async with self._redis.pipeline(transaction=True) as pipe:
            await pipe.delete("home:device-state-topics")
            if topic_map:
                await pipe.hset("home:device-state-topics", mapping=topic_map)
            await pipe.execute()

        logger.info("DeviceRegistry: loaded %d controllable device(s)", len(devices))

    async def watch(self) -> None:
        """Subscribe to 'devices:registry_changed' and reload on each message."""
        pubsub = self._redis.pubsub()
        await pubsub.subscribe("devices:registry_changed")
        logger.info("DeviceRegistry: watching for registry changes")
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True),
                        timeout=5.0,
                    )
                except TimeoutError:
                    msg = None
                if msg and msg["type"] == "message":
                    logger.info("DeviceRegistry: invalidating cache (registry changed)")
                    try:
                        await self.load()
                    except Exception:
                        logger.exception("DeviceRegistry: reload failed")
        finally:
            await pubsub.unsubscribe("devices:registry_changed")
            await pubsub.aclose()

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    async def all(self) -> list[ResolvedDevice]:
        """Return all cached controllable devices (snapshot)."""
        async with self._lock:
            return list(self._devices)

    async def find(
        self,
        kind: str | None = None,
        room_slug: str | None = None,
        room_alias_ua: str | None = None,
        device_alias_ua: str | None = None,
    ) -> list[ResolvedDevice]:
        """Return matching controllable devices; never raises.

        Matching rules (all supplied criteria must match simultaneously):
        - ``kind``: exact match (case-insensitive).
        - ``room_slug``: exact slug match.
        - ``room_alias_ua``: matches room slug, room.name, or any room alias
          (all case-folded).
        - ``device_alias_ua``: matches device label or any device alias
          (all case-folded).
        """
        async with self._lock:
            candidates = list(self._devices)

        if kind is not None:
            lk = kind.casefold()
            candidates = [d for d in candidates if d.kind.casefold() == lk]

        if room_slug is not None:
            rs = room_slug.casefold()
            candidates = [d for d in candidates if d.room_slug.casefold() == rs]

        if room_alias_ua is not None:
            query = room_alias_ua.casefold()
            candidates = [d for d in candidates if _room_matches(d, query)]

        if device_alias_ua is not None:
            query = device_alias_ua.casefold()
            candidates = [d for d in candidates if _device_matches(d, query)]

        return candidates

    async def rooms_with_aliases(self) -> dict[str, list[str]]:
        """Return slug → [room_name_ua, *room_aliases] for all rooms that have
        at least one controllable device."""
        async with self._lock:
            result: dict[str, list[str]] = {}
            for d in self._devices:
                if d.room_slug not in result:
                    result[d.room_slug] = [d.room_name_ua] + list(d.room_aliases)
            return result


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _room_matches(device: ResolvedDevice, query: str) -> bool:
    """True if query matches the room by slug, name, or any alias.

    Casefolding is applied to both stored values and the query, so callers
    do not need to pre-normalise the query string.
    """
    q = query.casefold()
    if device.room_slug.casefold() == q:
        return True
    if device.room_name_ua.casefold() == q:
        return True
    return any(a.casefold() == q for a in device.room_aliases)


def _device_matches(device: ResolvedDevice, query: str) -> bool:
    """True if query matches the device label or any device alias.

    Casefolding is applied to both stored values and the query.
    """
    q = query.casefold()
    if device.label and device.label.casefold() == q:
        return True
    return any(a.casefold() == q for a in device.device_aliases)
