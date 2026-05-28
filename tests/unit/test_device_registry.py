"""Unit tests for DeviceRegistry.

Uses in-memory mocks — no real DB or Redis required.
All async tests run via pytest-asyncio in auto mode (pyproject.toml).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hub.backend.services.device_registry import (
    DeviceRegistry,
    ResolvedDevice,
    _device_matches,
    _room_matches,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ROOM_ID = uuid.uuid4()
_PLACEMENT_ID = uuid.uuid4()


def _make_device(
    *,
    kind: str = "light",
    room_slug: str = "vitalnia",
    room_name_ua: str = "Вітальня",
    room_aliases: list[str] | None = None,
    device_aliases: list[str] | None = None,
    label: str | None = "Стеля",
    actions: list[str] | None = None,
) -> ResolvedDevice:
    return ResolvedDevice(
        placement_id=_PLACEMENT_ID,
        device_id="light-001",
        kind=kind,
        label=label,
        room_slug=room_slug,
        room_name_ua=room_name_ua,
        mqtt_command_topic=f"home/{room_slug}/{kind}/cmd",
        mqtt_state_topic=f"home/{room_slug}/{kind}/state",
        actions=actions or ["on", "off"],
        payload_on={"state": "on"},
        payload_off={"state": "off"},
        device_aliases=device_aliases or [],
        room_aliases=room_aliases or [],
    )


def _make_registry(devices: list[ResolvedDevice]) -> DeviceRegistry:
    """Build a DeviceRegistry with pre-populated cache (no DB needed)."""
    session_factory = MagicMock()
    redis_client = MagicMock()
    reg = DeviceRegistry(session_factory=session_factory, redis_client=redis_client)
    reg._devices = devices
    return reg


# ---------------------------------------------------------------------------
# _room_matches / _device_matches unit tests
# ---------------------------------------------------------------------------


def test_room_matches_by_slug() -> None:
    d = _make_device(room_slug="vitalnia")
    assert _room_matches(d, "vitalnia")
    assert not _room_matches(d, "spalnia")


def test_room_matches_by_name() -> None:
    d = _make_device(room_name_ua="Вітальня")
    assert _room_matches(d, "вітальня")


def test_room_matches_by_alias() -> None:
    d = _make_device(room_aliases=["зала", "велика кімната"])
    assert _room_matches(d, "зала")
    assert _room_matches(d, "Велика Кімната")  # case-fold
    assert not _room_matches(d, "спальня")


def test_device_matches_by_label() -> None:
    d = _make_device(label="Люстра")
    assert _device_matches(d, "люстра")
    assert not _device_matches(d, "лампа")


def test_device_matches_by_alias() -> None:
    d = _make_device(device_aliases=["лампа", "освітлення"])
    assert _device_matches(d, "лампа")
    assert not _device_matches(d, "вентилятор")


# ---------------------------------------------------------------------------
# DeviceRegistry.find tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_by_room_slug() -> None:
    devices = [
        _make_device(kind="light", room_slug="vitalnia"),
        _make_device(kind="relay", room_slug="kuhnia"),
    ]
    reg = _make_registry(devices)

    result = await reg.find(room_slug="vitalnia")
    assert len(result) == 1
    assert result[0].room_slug == "vitalnia"
    assert result[0].kind == "light"


@pytest.mark.asyncio
async def test_find_by_room_alias_ua() -> None:
    devices = [
        _make_device(room_slug="vitalnia", room_aliases=["зала"]),
    ]
    reg = _make_registry(devices)

    result = await reg.find(room_alias_ua="зала")
    assert len(result) == 1
    assert result[0].room_slug == "vitalnia"


@pytest.mark.asyncio
async def test_find_returns_empty_on_no_match() -> None:
    devices = [_make_device(kind="light", room_slug="vitalnia")]
    reg = _make_registry(devices)

    result = await reg.find(kind="thermostat")
    assert result == []


@pytest.mark.asyncio
async def test_find_never_raises() -> None:
    reg = _make_registry([])
    # No device in registry — should return [] not raise
    result = await reg.find(kind="light", room_alias_ua="зала")
    assert result == []


@pytest.mark.asyncio
async def test_find_kind_and_room_combined() -> None:
    devices = [
        _make_device(kind="light", room_slug="vitalnia"),
        _make_device(kind="relay", room_slug="vitalnia"),
        _make_device(kind="light", room_slug="spalnia"),
    ]
    reg = _make_registry(devices)

    result = await reg.find(kind="light", room_slug="vitalnia")
    assert len(result) == 1
    assert result[0].kind == "light"
    assert result[0].room_slug == "vitalnia"


@pytest.mark.asyncio
async def test_find_by_device_alias() -> None:
    devices = [
        _make_device(device_aliases=["люстра", "лампа"]),
    ]
    reg = _make_registry(devices)

    result = await reg.find(device_alias_ua="люстра")
    assert len(result) == 1

    result2 = await reg.find(device_alias_ua="вентилятор")
    assert result2 == []


@pytest.mark.asyncio
async def test_all_returns_snapshot() -> None:
    devices = [
        _make_device(kind="light"),
        _make_device(kind="relay"),
    ]
    reg = _make_registry(devices)

    all_devs = await reg.all()
    assert len(all_devs) == 2


# ---------------------------------------------------------------------------
# Cache invalidation via Redis pub/sub
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_invalidated_on_pubsub(monkeypatch: pytest.MonkeyPatch) -> None:
    """When 'devices:registry_changed' arrives, registry reloads from DB."""
    reg = _make_registry([_make_device(kind="light")])
    assert len(await reg.all()) == 1

    # Replace load() with a coroutine that clears the cache
    async def _fake_load() -> None:
        reg._devices = []

    monkeypatch.setattr(reg, "load", _fake_load)

    # Simulate one pub/sub message then stop
    message = {"type": "message", "data": ""}

    call_count = 0

    async def _fake_get_message(ignore_subscribe_messages: bool) -> dict | None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return message
        # After the first message raise to break the loop
        raise asyncio.CancelledError

    pubsub = AsyncMock()
    pubsub.get_message = _fake_get_message
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()
    pubsub.aclose = AsyncMock()

    monkeypatch.setattr(reg._redis, "pubsub", lambda: pubsub)

    with pytest.raises(asyncio.CancelledError):
        await reg.watch()

    # After the watch loop ran _fake_load, cache should be empty
    assert await reg.all() == []


# ---------------------------------------------------------------------------
# Only controllable=True rows loaded (integration with load())
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_filters_only_controllable(monkeypatch: pytest.MonkeyPatch) -> None:
    """DeviceRegistry.load() must only include controllable=True placements."""
    import types

    # Build minimal ORM-like mock objects
    def _make_placement(controllable: bool) -> Any:
        p = types.SimpleNamespace(
            id=uuid.uuid4(),
            device_id=f"dev-{uuid.uuid4()}",
            kind="light",
            label=None,
            config={},
            aliases=[],
            controllable=controllable,
            actions=["on", "off"],
        )
        return p

    def _make_room() -> Any:
        r = types.SimpleNamespace(
            slug="vitalnia",
            name="Вітальня",
            aliases=[],
        )
        return r

    p_ctrl = _make_placement(controllable=True)
    _make_placement(controllable=False)  # non-controllable — must be filtered out
    room = _make_room()

    # Mock session_factory → session → execute → scalars/all
    mock_result = MagicMock()
    mock_result.all.return_value = [(p_ctrl, room)]  # only controllable row returned

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock()
    mock_factory.return_value = mock_session

    redis_client = MagicMock()
    reg = DeviceRegistry(session_factory=mock_factory, redis_client=redis_client)

    await reg.load()

    devices = await reg.all()
    assert len(devices) == 1
    assert devices[0].device_id == p_ctrl.device_id


# ---------------------------------------------------------------------------
# rooms_with_aliases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rooms_with_aliases() -> None:
    devices = [
        _make_device(room_slug="vitalnia", room_name_ua="Вітальня", room_aliases=["зала"]),
        _make_device(
            kind="relay", room_slug="vitalnia", room_name_ua="Вітальня", room_aliases=["зала"]
        ),
        _make_device(kind="light", room_slug="spalnia", room_name_ua="Спальня", room_aliases=[]),
    ]
    reg = _make_registry(devices)
    result = await reg.rooms_with_aliases()

    assert "vitalnia" in result
    assert "зала" in result["vitalnia"]
    assert "Вітальня" in result["vitalnia"]
    assert "spalnia" in result
    assert result["spalnia"] == ["Спальня"]
