"""Unit tests for hub/edge/agent/scene_engine.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hub.edge.agent.scene_engine import SceneEngine, _build_payload

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_YAML = """\
scenes:
  кіно:
    aliases: [кіноперегляд, фільм, cinema]
    description: Приглушене освітлення
    actions:
      - {kind: light, action: brightness_set, value: 15}

  ніч:
    aliases: [спати, час спати, night]
    description: Вимкнути все
    actions:
      - {kind: light, action: off}
      - {kind: relay, action: off}
"""


def _engine(tmp_path: Path, yaml_text: str = _YAML) -> SceneEngine:
    p = tmp_path / "scenes.yaml"
    p.write_text(yaml_text)
    eng = SceneEngine(scenes_path=p)
    eng.load()
    return eng


def _device(kind: str = "light", room_slug: str = "vitalnya") -> MagicMock:
    d = MagicMock()
    d.kind = kind
    d.room_slug = room_slug
    d.device_id = f"{kind}-{room_slug}"
    d.mqtt_command_topic = f"home/{room_slug}/{kind}/cmd"
    d.payload_on = {"state": "ON"}
    d.payload_off = {"state": "OFF"}
    return d


def _registry(*devices: MagicMock) -> MagicMock:
    reg = MagicMock()
    reg.all = AsyncMock(return_value=list(devices))
    return reg


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def test_load_reads_scenes(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    assert eng.is_loaded
    assert "кіно" in eng.scene_names
    assert "ніч" in eng.scene_names


def test_load_missing_file_does_not_raise(tmp_path: Path) -> None:
    eng = SceneEngine(scenes_path=tmp_path / "nope.yaml")
    eng.load()
    assert not eng.is_loaded


def test_scene_names_returns_all_keys(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    assert set(eng.scene_names) == {"кіно", "ніч"}


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def test_match_by_alias_substring(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    assert eng.match("активуй кіноперегляд зараз") == "кіно"


def test_match_by_scene_name(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    assert eng.match("режим кіно") == "кіно"


def test_match_prefers_longer_alias(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    # "час спати" is longer than "спати" — should still match "ніч"
    assert eng.match("час спати будь ласка") == "ніч"


def test_match_returns_none_for_unknown(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    assert eng.match("яка температура у кімнаті") is None


def test_match_english_alias(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    assert eng.match("enable cinema mode") == "кіно"


# ---------------------------------------------------------------------------
# _build_payload
# ---------------------------------------------------------------------------


def test_build_payload_on(tmp_path: Path) -> None:
    d = _device()
    p = _build_payload(d, "on", None)
    assert p == {"state": "ON"}


def test_build_payload_off(tmp_path: Path) -> None:
    d = _device()
    p = _build_payload(d, "off", None)
    assert p == {"state": "OFF"}


def test_build_payload_brightness_set(tmp_path: Path) -> None:
    d = _device()
    p = _build_payload(d, "brightness_set", 50)
    assert p is not None
    assert p["brightness"] == round(50 * 255 / 100)


def test_build_payload_brightness_zero(tmp_path: Path) -> None:
    d = _device()
    p = _build_payload(d, "brightness_set", 0)
    assert p is not None and p["brightness"] == 0


def test_build_payload_brightness_100(tmp_path: Path) -> None:
    d = _device()
    p = _build_payload(d, "brightness_set", 100)
    assert p is not None and p["brightness"] == 255


def test_build_payload_brightness_missing_value_returns_none(tmp_path: Path) -> None:
    d = _device()
    assert _build_payload(d, "brightness_set", None) is None


def test_build_payload_unknown_action_returns_none(tmp_path: Path) -> None:
    d = _device()
    assert _build_payload(d, "dance", None) is None


# ---------------------------------------------------------------------------
# plan() — async
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_returns_tool_calls_for_matching_devices(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    light = _device("light", "vitalnya")
    reg = _registry(light)

    calls = await eng.plan("кіно", registry=reg)
    assert len(calls) == 1
    assert calls[0].tool == "mqtt_publish"
    assert calls[0].topic == "home/vitalnya/light/cmd"
    assert calls[0].payload is not None
    assert "brightness" in calls[0].payload


@pytest.mark.asyncio
async def test_plan_filters_by_kind(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    light = _device("light")
    relay = _device("relay")
    reg = _registry(light, relay)

    # кіно only targets kind=light
    calls = await eng.plan("кіно", registry=reg)
    assert all(c.topic and "light" in c.topic for c in calls)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_plan_ніч_targets_light_and_relay(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    light = _device("light")
    relay = _device("relay")
    reg = _registry(light, relay)

    calls = await eng.plan("ніч", registry=reg)
    # Two actions (off for each kind), each with one device
    assert len(calls) == 2
    tools = {c.tool for c in calls}
    assert tools == {"mqtt_publish"}
    payloads = [c.payload for c in calls]
    assert all(p == {"state": "OFF"} for p in payloads)


@pytest.mark.asyncio
async def test_plan_filters_by_speaker_room(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    light_v = _device("light", "vitalnya")
    light_k = _device("light", "kukhnya")
    reg = _registry(light_v, light_k)

    calls = await eng.plan("кіно", registry=reg, speaker_room="vitalnya")
    assert len(calls) == 1
    assert "vitalnya" in (calls[0].topic or "")


@pytest.mark.asyncio
async def test_plan_falls_back_to_all_when_room_has_no_devices(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    light = _device("light", "kukhnya")
    reg = _registry(light)

    # speaker_room="vitalnya" but no lights there → fallback to all lights
    calls = await eng.plan("кіно", registry=reg, speaker_room="vitalnya")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_plan_unknown_scene_returns_empty(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    calls = await eng.plan("невідома сцена", registry=_registry())
    assert calls == []


@pytest.mark.asyncio
async def test_plan_no_matching_devices_returns_empty(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    reg = _registry()  # no devices at all
    calls = await eng.plan("кіно", registry=reg)
    assert calls == []


# ---------------------------------------------------------------------------
# description()
# ---------------------------------------------------------------------------


def test_description_returns_yaml_value(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    assert "освітлення" in eng.description("кіно")


def test_description_unknown_scene_returns_name(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    assert eng.description("nonexistent") == "nonexistent"
