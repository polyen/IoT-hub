"""Unit tests for hub.edge.agent.text_resolver.TextResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from hub.edge.agent.text_resolver import (
    Resolution,
    ResolutionFailureKind,
    TextResolver,
    _extract_action,
    _extract_kind,
    _extract_numeric_param,
    _extract_room_slug,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_device(
    device_id: str = "light-01",
    kind: str = "light",
    room_slug: str = "living-room",
    room_name_ua: str = "Вітальня",
    label: str | None = "Люстра",
    actions: list[str] | None = None,
    device_aliases: list[str] | None = None,
    room_aliases: list[str] | None = None,
    payload_on: dict | None = None,
    payload_off: dict | None = None,
    mqtt_command_topic: str = "home/living-room/light/cmd",
) -> MagicMock:
    d = MagicMock()
    d.device_id = device_id
    d.kind = kind
    d.room_slug = room_slug
    d.room_name_ua = room_name_ua
    d.label = label
    d.actions = actions or ["on", "off", "toggle"]
    d.device_aliases = device_aliases or ["люстра", "світло"]
    d.room_aliases = room_aliases or ["вітальня", "зал"]
    d.payload_on = payload_on or {"state": "ON"}
    d.payload_off = payload_off or {"state": "OFF"}
    d.mqtt_command_topic = mqtt_command_topic
    return d


def make_registry(
    devices: list | None = None,
    rooms: dict[str, list[str]] | None = None,
) -> MagicMock:
    registry = MagicMock()
    _devices = devices if devices is not None else [make_device()]
    _rooms = rooms if rooms is not None else {"living-room": ["Вітальня", "вітальня", "зал"]}

    registry.all = AsyncMock(return_value=_devices)
    registry.rooms_with_aliases = AsyncMock(return_value=_rooms)

    # registry.find returns subset filtered by kind and room_slug
    async def find_impl(
        kind: str | None = None,
        room_slug: str | None = None,
        room_alias_ua: str | None = None,
        device_alias_ua: str | None = None,
    ) -> list:
        result = list(_devices)
        if kind is not None:
            result = [d for d in result if d.kind.casefold() == kind.casefold()]
        if room_slug is not None:
            result = [d for d in result if d.room_slug.casefold() == room_slug.casefold()]
        return result

    registry.find = AsyncMock(side_effect=find_impl)
    return registry


# ---------------------------------------------------------------------------
# Unit: action extraction
# ---------------------------------------------------------------------------


def test_extract_action_on() -> None:
    assert _extract_action("увімкни світло") == "on"


def test_extract_action_off() -> None:
    assert _extract_action("вимкни все") == "off"


def test_extract_action_toggle() -> None:
    assert _extract_action("перемкни реле") == "toggle"


def test_extract_action_open() -> None:
    assert _extract_action("відкрий двері") == "open"


def test_extract_action_close() -> None:
    assert _extract_action("закрий жалюзі") == "close"


def test_extract_action_none_for_query() -> None:
    assert _extract_action("яка температура в кімнаті") is None


# ---------------------------------------------------------------------------
# Unit: numeric param extraction
# ---------------------------------------------------------------------------


def test_extract_numeric_percent() -> None:
    p = _extract_numeric_param("встанови яскравість на 60 відсотків")
    assert p == {"value": 60, "unit": "%"}


def test_extract_numeric_celsius() -> None:
    p = _extract_numeric_param("встанови температуру 22 градуси")
    assert p == {"value": 22, "unit": "celsius"}


def test_extract_numeric_bare() -> None:
    p = _extract_numeric_param("встанови на 75")
    assert p == {"value": 75}


# ---------------------------------------------------------------------------
# Unit: room slug extraction
# ---------------------------------------------------------------------------


def test_extract_room_slug_with_prep() -> None:
    rooms = {"living-room": ["Вітальня", "вітальня", "зал"]}
    slug = _extract_room_slug("увімкни світло у вітальні", rooms)
    assert slug == "living-room"


def test_extract_room_slug_bare() -> None:
    rooms = {"bedroom": ["Спальня", "спальня"]}
    slug = _extract_room_slug("вимкни спальня все", rooms)
    assert slug == "bedroom"


def test_extract_room_slug_none_when_no_match() -> None:
    rooms = {"bedroom": ["Спальня", "спальня"]}
    slug = _extract_room_slug("вимкни кухня все", rooms)
    assert slug is None


# ---------------------------------------------------------------------------
# Unit: kind extraction
# ---------------------------------------------------------------------------


def test_extract_kind_light() -> None:
    kind = _extract_kind("увімкни лампу", {})
    assert kind == "light"


def test_extract_kind_relay() -> None:
    kind = _extract_kind("вимкни розетку", {})
    assert kind == "relay"


def test_extract_kind_lock() -> None:
    kind = _extract_kind("відкрий двері", {})
    assert kind == "lock"


# ---------------------------------------------------------------------------
# Integration: TextResolver.resolve()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_simple_light_on() -> None:
    device = make_device()
    registry = make_registry(devices=[device])
    resolver = TextResolver(registry)

    resolution = await resolver.resolve("увімкни світло у вітальні")

    assert resolution.success is True
    assert resolution.action == "on"
    assert resolution.device is device


@pytest.mark.asyncio
async def test_resolve_uses_prototype_when_action_missing() -> None:
    device = make_device()
    registry = make_registry(devices=[device])
    resolver = TextResolver(registry)

    # No action verb, but prototype gives "light_on" hint
    resolution = await resolver.resolve("люстра вітальня", prototype="light_on")

    assert resolution.success is True
    assert resolution.action == "on"


@pytest.mark.asyncio
async def test_resolve_falls_back_to_speaker_room() -> None:
    device = make_device(room_slug="bedroom")
    registry = make_registry(
        devices=[device],
        rooms={"bedroom": ["Спальня", "спальня"]},
    )
    resolver = TextResolver(registry)

    # No room mentioned in text; speaker_room fills in
    resolution = await resolver.resolve("вимкни світло", speaker_room="bedroom")

    assert resolution.success is True
    assert resolution.device.room_slug == "bedroom"


@pytest.mark.asyncio
async def test_resolve_broadcast() -> None:
    d1 = make_device(device_id="light-01", room_slug="living-room")
    d2 = make_device(device_id="light-02", room_slug="living-room")
    registry = make_registry(devices=[d1, d2])
    resolver = TextResolver(registry)

    resolution = await resolver.resolve("вимкни все у вітальні")

    assert resolution.success is True
    assert len(resolution.all_devices) == 2
    assert resolution.action == "off"


@pytest.mark.asyncio
async def test_resolve_device_not_found() -> None:
    registry = make_registry(devices=[])
    resolver = TextResolver(registry)

    resolution = await resolver.resolve("увімкни світло")

    assert resolution.success is False
    assert resolution.failure_kind == ResolutionFailureKind.DEVICE_NOT_FOUND


@pytest.mark.asyncio
async def test_resolve_ambiguous() -> None:
    d1 = make_device(device_id="light-01")
    d2 = make_device(device_id="light-02")
    registry = make_registry(devices=[d1, d2])
    resolver = TextResolver(registry)

    # Both are in living-room, no further disambiguation
    resolution = await resolver.resolve("увімкни світло у вітальні")

    assert resolution.success is False
    assert resolution.failure_kind == ResolutionFailureKind.AMBIGUOUS
    assert len(resolution.candidates) == 2


@pytest.mark.asyncio
async def test_resolve_unsupported_action() -> None:
    device = make_device(actions=["on", "off"])  # toggle not allowed
    registry = make_registry(devices=[device])
    resolver = TextResolver(registry)

    resolution = await resolver.resolve("перемкни світло у вітальні")

    assert resolution.success is False
    assert resolution.failure_kind == ResolutionFailureKind.UNSUPPORTED_ACTION


@pytest.mark.asyncio
async def test_resolve_unclear_intent_no_action() -> None:
    registry = make_registry()
    resolver = TextResolver(registry)

    resolution = await resolver.resolve("яка погода")

    assert resolution.success is False
    assert resolution.failure_kind == ResolutionFailureKind.UNCLEAR_INTENT


@pytest.mark.asyncio
async def test_resolve_with_params() -> None:
    device = make_device(kind="light", actions=["brightness_set"])
    registry = make_registry(devices=[device])
    resolver = TextResolver(registry)

    resolution = await resolver.resolve(
        "встанови яскравість на 60 відсотків у вітальні",
        prototype="brightness_set",
    )

    assert resolution.success is True
    assert resolution.action == "brightness_set"
    assert resolution.params.get("value") == 60


@pytest.mark.asyncio
async def test_resolve_never_raises() -> None:
    """Even a completely broken registry must not propagate exceptions."""
    registry = MagicMock()
    registry.all = AsyncMock(side_effect=RuntimeError("DB is down"))
    registry.rooms_with_aliases = AsyncMock(side_effect=RuntimeError("DB is down"))
    registry.find = AsyncMock(side_effect=RuntimeError("DB is down"))

    resolver = TextResolver(registry)
    resolution = await resolver.resolve("увімкни щось")

    # Must return a failure Resolution, not raise
    assert isinstance(resolution, Resolution)
    assert resolution.success is False
