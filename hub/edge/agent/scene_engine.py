"""Scene engine: spoken scene names → multi-device MQTT action plans.

Loads hub/scenes.yaml at startup.  The ML intent classifier routes
"scene_generic" utterances here; the engine matches the utterance text
against scene aliases and returns a list of ToolCalls that the orchestrator
executes through the normal PolicyEngine path (so DENY rules are respected
and every action gets an AgentAudit row).

Design notes:
- SceneEngine.plan() is async because it calls DeviceRegistry.all().
- SceneEngine does NOT publish MQTT directly — it only plans ToolCalls.
- Room filtering: if speaker_room is known (ArcFace), the scene targets only
  devices in that room; otherwise all devices of the required kind.
- Brightness is stored in YAML as 0-100 % and converted to 0-255 (HA MQTT).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from hub.edge.agent.policy import ToolCall

logger = logging.getLogger(__name__)

# Default scenes file location (relative to repo root / working dir)
_DEFAULT_SCENES_PATH = Path("hub/scenes.yaml")


@dataclass
class SceneResult:
    scene_name: str
    description: str
    n_tool_calls: int


class SceneEngine:
    """Match utterances against scene aliases and build per-device ToolCall lists."""

    def __init__(self, scenes_path: Path = _DEFAULT_SCENES_PATH) -> None:
        self._path = scenes_path
        self._scenes: dict[str, dict[str, Any]] = {}

    def load(self) -> None:
        if not self._path.exists():
            logger.warning("SceneEngine: scenes file not found at %s", self._path)
            return
        try:
            raw: dict[str, Any] = yaml.safe_load(self._path.read_text()) or {}
            self._scenes = raw.get("scenes", {})
            logger.info("SceneEngine: loaded %d scenes from %s", len(self._scenes), self._path)
        except Exception as exc:
            logger.warning("SceneEngine: failed to load %s: %s", self._path, exc)

    @property
    def is_loaded(self) -> bool:
        return bool(self._scenes)

    @property
    def scene_names(self) -> list[str]:
        return list(self._scenes.keys())

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def match(self, text: str) -> str | None:
        """Return the first scene name whose alias (or name itself) appears in text.

        Prefers longer alias matches to avoid false positives on short names.
        """
        lower = text.lower()
        best_name: str | None = None
        best_len: int = 0

        for name, scene in self._scenes.items():
            # Match on scene name slug itself
            if name in lower and len(name) > best_len:
                best_name = name
                best_len = len(name)
            # Match on each alias (prefer longer)
            for alias in scene.get("aliases", []):
                a = alias.lower()
                if a in lower and len(a) > best_len:
                    best_name = name
                    best_len = len(a)

        return best_name

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------

    async def plan(
        self,
        scene_name: str,
        registry: Any,  # DeviceRegistry
        speaker_room: str | None = None,
    ) -> list[ToolCall]:
        """Return ordered ToolCalls for executing scene_name.

        Devices are filtered by kind and optionally by speaker_room.
        If speaker_room is set but no devices found in that room,
        falls back to all devices of the required kind.
        """
        scene = self._scenes.get(scene_name)
        if not scene:
            logger.warning("SceneEngine: unknown scene %r", scene_name)
            return []

        all_devices = await registry.all()
        tool_calls: list[ToolCall] = []

        for spec in scene.get("actions", []):
            kind: str | None = spec.get("kind")
            action: Any = spec.get("action", "on")
            value: int | None = spec.get("value")
            spec_room: str | None = spec.get("room") or speaker_room

            # Filter by kind
            candidates = [d for d in all_devices if not kind or d.kind == kind]

            # Filter by room (with fallback to all-room if no match)
            if spec_room:
                in_room = [d for d in candidates if d.room_slug == spec_room]
                if in_room:
                    candidates = in_room

            for device in candidates:
                payload = _build_payload(device, action, value)
                if payload is None:
                    continue
                tool_calls.append(
                    ToolCall(tool="mqtt_publish", topic=device.mqtt_command_topic, payload=payload)
                )

        logger.info(
            "SceneEngine: %r planned %d tool-calls (speaker_room=%r)",
            scene_name,
            len(tool_calls),
            speaker_room,
        )
        return tool_calls

    def description(self, scene_name: str) -> str:
        return str(self._scenes.get(scene_name, {}).get("description", scene_name))


# ------------------------------------------------------------------
# Payload helpers
# ------------------------------------------------------------------


def _build_payload(device: Any, action: Any, value: int | None) -> dict[str, Any] | None:
    """Build MQTT payload for one device action.

    ``action`` is normalised to str because YAML parses bare ``on``/``off``
    as booleans; callers should quote them in YAML, but we defend here too.
    """
    act = str(action).lower() if not isinstance(action, str) else action
    if act == "on" or action is True:
        return dict(device.payload_on) if device.payload_on else {"state": "ON"}
    if act == "off" or action is False:
        return dict(device.payload_off) if device.payload_off else {"state": "OFF"}
    if act == "brightness_set":
        if value is None:
            logger.warning("SceneEngine: brightness_set missing value for %s", device.device_id)
            return None
        brightness = max(0, min(255, round(value * 255 / 100)))
        return {"brightness": brightness}
    logger.warning("SceneEngine: unknown action %r for device %s", action, device.device_id)
    return None
