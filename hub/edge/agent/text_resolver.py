"""Parse UA voice command text to (action, device, params) using rules.

Pipeline (short-circuits on success):
1. Action extractor — UA verb stems → canonical action
2. Device-kind extractor — registry aliases + static map → kind string
3. Room extractor — registry rooms_with_aliases + UA prepositions → room slug
4. All-broadcast detection — "все" / "всі" keyword
5. Param extractor — numeric values for set/brightness/temp actions
6. Resolve via DeviceRegistry.find()
7. Validate action against device.actions

Returns a Resolution dataclass. Never raises.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hub.backend.services.device_registry import DeviceRegistry, ResolvedDevice

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class ResolutionFailureKind(StrEnum):
    UNCLEAR_INTENT = "unclear_intent"
    UNKNOWN_DEVICE_KIND = "unknown_device_kind"
    DEVICE_NOT_FOUND = "device_not_found"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED_ACTION = "unsupported_action"


@dataclass(frozen=True)
class Resolution:
    success: bool
    action: str | None = None
    device: ResolvedDevice | None = None
    params: dict[str, Any] = field(default_factory=dict)
    failure_kind: ResolutionFailureKind | None = None
    failure_context: dict[str, str] = field(default_factory=dict)
    # For AMBIGUOUS: all matched candidates
    candidates: list[ResolvedDevice] = field(default_factory=list)
    # For broadcast ("вимкни все"): all matched devices
    all_devices: list[ResolvedDevice] = field(default_factory=list)
    # Human-readable trace for UI / logs
    reasoning: str | None = None


# ---------------------------------------------------------------------------
# Static action extraction tables
# ---------------------------------------------------------------------------

# Verb stem → canonical action
# NOTE: nouns ("температур", "яскравість") are NOT here — they're domain qualifiers,
# not action verbs. A command like "яка температура" should return action=None.
_ACTION_STEMS: list[tuple[list[str], str]] = [
    (["збільш", "increase", "підвищ"], "inc"),
    (["зменш", "decrease", "зниз"], "dec"),
    (["встанови", "set "], "set"),
    (["перемкн", "toggle", "switch"], "toggle"),
    (["відкрий", "відкр", "open"], "open"),
    (["закрий", "закр", "close"], "close"),
    (["увімкн", "turn on", "вмикай"], "on"),
    (["вимкн", "turn off", "вимикай"], "off"),
]

# Static device-kind aliases (augmented dynamically from registry)
_STATIC_KIND_ALIASES: dict[str, list[str]] = {
    "light": ["світло", "лампа", "ліхтар", "освітлення", "люстра", "light"],
    "relay": ["реле", "розетка", "вентилятор", "relay", "switch"],
    "lock": ["замок", "lock", "двері", "door"],
    "thermostat": ["термостат", "thermostat", "кондиціонер", "обігрівач", "heater", "ac"],
    "camera": ["камера", "camera"],
    "speaker": ["динамік", "speaker", "колонка"],
}

# UA prepositions preceding room name in a command
_ROOM_PREPS = ["у ", "в ", "на ", "біля ", "до ", "з ", "із "]

# Broadcast keywords
_BROADCAST_KW = ["все", "всі", "all"]


# ---------------------------------------------------------------------------
# Helper: extract action from text
# ---------------------------------------------------------------------------


def _extract_action(lower: str) -> str | None:
    """Extract canonical action from lowercased text.

    After a base verb is found, upgrades "set" to "temp_set" or "brightness_set"
    based on domain noun context, so pure noun queries (no verb) return None.
    """
    action: str | None = None
    for kws, act in _ACTION_STEMS:
        if any(kw in lower for kw in kws):
            action = act
            break
    if action is None:
        return None
    # Upgrade "set" / "inc" / "dec" to domain-specific actions
    if action in ("set", "inc", "dec"):
        if any(kw in lower for kw in ("температур", "градус", "°", "temperature")):
            action = "temp_set"
        elif any(kw in lower for kw in ("яскравість", "відсоток", "відсотків", "brightness")):
            action = "brightness_set"
    return action


# ---------------------------------------------------------------------------
# Helper: extract device kind from text
# ---------------------------------------------------------------------------


def _stem(s: str) -> str:
    """Simple Ukrainian declension stem: strip last 2 chars if word is long enough."""
    cf = s.casefold()
    return cf[: max(4, len(cf) - 2)] if len(cf) > 4 else cf


def _extract_kind(
    lower: str,
    registry_alias_map: dict[str, list[str]],
) -> str | None:
    """Return canonical device kind string or None.

    Uses stem matching to handle Ukrainian noun declensions (e.g. "лампа"→"лампу").
    Checks static map first, then dynamic registry aliases.
    """
    for kind, aliases in _STATIC_KIND_ALIASES.items():
        if any(_stem(a) in lower for a in aliases):
            return kind
    for kind, aliases in registry_alias_map.items():
        if any(_stem(a) in lower for a in aliases):
            return kind
    return None


# ---------------------------------------------------------------------------
# Helper: extract room slug from text (prep + room name / alias)
# ---------------------------------------------------------------------------


def _extract_room_slug(
    lower: str,
    rooms_with_aliases: dict[str, list[str]],
) -> str | None:
    """Return room slug or None.

    Scans for preposition + room name/alias pattern OR bare room name/alias.
    """
    # Build (slug, [all_forms]) sorted by alias length descending (longest first
    # avoids ambiguous partial matches — e.g. "спальня" before "спаль")
    candidates: list[tuple[str, list[str]]] = sorted(
        rooms_with_aliases.items(),
        key=lambda x: max((len(a) for a in x[1]), default=0),
        reverse=True,
    )

    for slug, forms in candidates:
        for form in forms:
            form_lower = form.casefold()
            stem = _stem(form_lower)
            # Try with preposition (stem match handles locative case, e.g. "вітальня"→"вітальні")
            for prep in _ROOM_PREPS:
                if (prep + stem) in lower:
                    return slug
            # Bare match (only if stem is 4+ chars to avoid false positives)
            if len(stem) >= 4 and stem in lower:
                return slug
    return None


# ---------------------------------------------------------------------------
# Helper: extract numeric param
# ---------------------------------------------------------------------------


def _extract_numeric_param(lower: str) -> dict[str, Any]:
    """Extract value for set actions like 'на 60 відсотків' or '22 градуси'."""
    match = re.search(r"\b(\d+)\s*(відсоток|відсотків|%|градус|°)", lower)
    if match:
        value = int(match.group(1))
        unit_raw = match.group(2)
        unit = "%" if unit_raw in ("%", "відсоток", "відсотків") else "celsius"
        return {"value": value, "unit": unit}
    # bare number fallback
    match = re.search(r"\b(\d+)\b", lower)
    if match:
        return {"value": int(match.group(1))}
    return {}


# ---------------------------------------------------------------------------
# TextResolver
# ---------------------------------------------------------------------------


class TextResolver:
    """Rules-based UA text → (action, device) resolver.

    ``registry`` must have been loaded before calling ``resolve``.
    Pass ``llm=None`` for Phase 2 (LLM fallback lives in Phase 5).
    """

    def __init__(
        self,
        registry: DeviceRegistry,
        llm: Any | None = None,
    ) -> None:
        self._registry = registry
        self._llm = llm  # reserved for Phase 5

    async def resolve(
        self,
        text: str,
        prototype: str | None = None,
        speaker_room: str | None = None,
    ) -> Resolution:
        """Parse ``text`` into a Resolution.

        ``speaker_room``: room slug of the speaker (from ArcFace identity,
        used as fallback when the command omits a room name).
        """
        try:
            return await self._resolve_inner(text, prototype, speaker_room)
        except Exception:
            logger.exception("TextResolver.resolve() raised unexpectedly for %r", text)
            return Resolution(
                success=False,
                failure_kind=ResolutionFailureKind.UNCLEAR_INTENT,
                reasoning="Internal resolver error",
            )

    # ------------------------------------------------------------------
    # Internal pipeline
    # ------------------------------------------------------------------

    async def _resolve_inner(
        self,
        text: str,
        prototype: str | None,
        speaker_room: str | None,
    ) -> Resolution:
        lower = text.casefold()

        # 1. Action extraction
        action = _extract_action(lower)
        if action is None:
            # Use prototype hint from router as fallback
            if prototype:
                action = self._action_from_prototype(prototype)
        if action is None:
            return Resolution(
                success=False,
                failure_kind=ResolutionFailureKind.UNCLEAR_INTENT,
                reasoning=f"No action verb found in: {text!r}",
            )

        # 2. Broadcast detection ("вимкни все")
        is_broadcast = any(kw in lower for kw in _BROADCAST_KW)

        # 3. Build dynamic alias map from registry
        all_devices = await self._registry.all()
        registry_alias_map = self._build_registry_alias_map(all_devices)

        # 4. Device-kind extraction
        kind = _extract_kind(lower, registry_alias_map)

        # If kind not found from text but prototype gives a hint, use it
        if kind is None and prototype:
            kind = self._kind_from_prototype(prototype)

        # 5. Room extraction
        rooms = await self._registry.rooms_with_aliases()
        room_slug = _extract_room_slug(lower, rooms)

        # Use speaker room as fallback if no room mentioned in text
        if room_slug is None and speaker_room:
            room_slug = speaker_room
            logger.debug("TextResolver: using speaker room %r as fallback", speaker_room)

        # 6. Param extraction (for set/brightness/temp actions)
        params: dict[str, Any] = {}
        if action in ("set", "brightness_set", "temp_set", "inc", "dec"):
            params = _extract_numeric_param(lower)

        # 7. Broadcast path: find all controllable devices (optionally filtered by kind/room)
        if is_broadcast:
            return await self._resolve_broadcast(action, kind, room_slug, params)

        # 8. Kind must be known at this point (after all hints exhausted)
        if kind is None:
            # Gather known kinds from registry
            known = list({d.kind for d in all_devices})
            from hub.edge.agent.i18n_uk import KIND_UA, known_kinds_ua

            kind_text = self._extract_unknown_kind_text(lower)
            return Resolution(
                success=False,
                failure_kind=ResolutionFailureKind.UNKNOWN_DEVICE_KIND,
                failure_context={
                    "kind_text": kind_text or text,
                    "known_kinds_ua": known_kinds_ua(known),
                },
                reasoning=f"Could not determine device kind from: {text!r}",
            )

        # 9. Resolve via registry
        candidates = await self._registry.find(
            kind=kind,
            room_slug=room_slug,
        )

        if not candidates and room_slug is None:
            # Try without room filter (no room in text and no speaker room)
            candidates = await self._registry.find(kind=kind)

        if not candidates:
            from hub.edge.agent.i18n_uk import KIND_UA

            if room_slug:
                room_display = rooms.get(room_slug, [room_slug])[0]
                room_part = f" у кімнаті «{room_display}»"
            else:
                room_part = ""
            return Resolution(
                success=False,
                failure_kind=ResolutionFailureKind.DEVICE_NOT_FOUND,
                failure_context={
                    "kind_ua": KIND_UA.get(kind, kind),
                    "room_part": room_part,
                },
                reasoning=(
                    f"No controllable {kind!r} found"
                    + (f" in room {room_slug!r}" if room_slug else "")
                ),
            )

        if len(candidates) > 1:
            # Ambiguous: more than one device matches
            labels = ", ".join(f"«{d.label or d.device_id}»" for d in candidates[:5])
            room_name = (
                rooms.get(room_slug, [room_slug or "кімнаті"])[0] if room_slug else "кімнаті"
            )
            return Resolution(
                success=False,
                failure_kind=ResolutionFailureKind.AMBIGUOUS,
                candidates=candidates,
                failure_context={
                    "room_ua": room_name,
                    "candidate_labels": labels,
                },
                reasoning=f"Ambiguous: {len(candidates)} devices match kind={kind!r} room={room_slug!r}",
            )

        device = candidates[0]

        # 10. Validate action against device.actions
        if device.actions and action not in device.actions:
            from hub.edge.agent.i18n_uk import ACTION_UA

            available = ", ".join(f"«{ACTION_UA.get(a, a)}»" for a in device.actions)
            return Resolution(
                success=False,
                failure_kind=ResolutionFailureKind.UNSUPPORTED_ACTION,
                failure_context={
                    "device_label": device.label or device.device_id,
                    "action_ua": ACTION_UA.get(action, action),
                    "available_actions_ua": available,
                },
                reasoning=f"Action {action!r} not in device.actions {device.actions!r}",
            )

        return Resolution(
            success=True,
            action=action,
            device=device,
            params=params,
            reasoning=(
                f"Resolved: action={action!r} kind={kind!r} room={room_slug!r} "
                f"→ device {device.device_id!r}"
            ),
        )

    # ------------------------------------------------------------------
    # Broadcast resolution
    # ------------------------------------------------------------------

    async def _resolve_broadcast(
        self,
        action: str,
        kind: str | None,
        room_slug: str | None,
        params: dict[str, Any],
    ) -> Resolution:
        devices = await self._registry.find(kind=kind, room_slug=room_slug)
        if not devices:
            # Retry without room filter
            devices = await self._registry.find(kind=kind)
        if not devices:
            from hub.edge.agent.i18n_uk import KIND_UA

            return Resolution(
                success=False,
                failure_kind=ResolutionFailureKind.DEVICE_NOT_FOUND,
                failure_context={
                    "kind_ua": KIND_UA.get(kind or "", kind or "пристрій"),
                    "room_part": "",
                },
                reasoning=f"Broadcast: no controllable devices found for kind={kind!r}",
            )
        # Use first device as primary (for topic/payload reference); all_devices for execution
        return Resolution(
            success=True,
            action=action,
            device=devices[0],
            all_devices=devices,
            params=params,
            reasoning=(
                f"Broadcast: action={action!r} → {len(devices)} device(s)"
                + (f" in room {room_slug!r}" if room_slug else "")
            ),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _action_from_prototype(prototype: str) -> str | None:
        """Map router prototype label to canonical action."""
        mapping = {
            "light_on": "on",
            "light_off": "off",
            "light_toggle": "toggle",
            "relay_on": "on",
            "relay_off": "off",
            "relay_toggle": "toggle",
            "device_on": "on",
            "device_off": "off",
            "on_all": "on",
            "off_all": "off",
            "blinds_open": "open",
            "blinds_close": "close",
            "door_open": "open",
            "door_close": "close",
            "timer_set": None,  # timer handled elsewhere
            "temp_set": "temp_set",
            "brightness_set": "brightness_set",
            "volume_set": "set",
        }
        return mapping.get(prototype)

    @staticmethod
    def _kind_from_prototype(prototype: str) -> str | None:
        """Map router prototype label to device kind."""
        mapping = {
            "light_on": "light",
            "light_off": "light",
            "light_toggle": "light",
            "relay_on": "relay",
            "relay_off": "relay",
            "relay_toggle": "relay",
            "blinds_open": "relay",
            "blinds_close": "relay",
            "door_open": "lock",
            "door_close": "lock",
            "temp_set": "thermostat",
            "brightness_set": "light",
        }
        return mapping.get(prototype)

    @staticmethod
    def _build_registry_alias_map(
        all_devices: list[ResolvedDevice],
    ) -> dict[str, list[str]]:
        """Build kind → [alias, ...] from registry for dynamic kind extraction."""
        result: dict[str, list[str]] = {}
        for d in all_devices:
            if d.kind not in result:
                result[d.kind] = []
            result[d.kind].extend(d.device_aliases)
        return result

    @staticmethod
    def _extract_unknown_kind_text(lower: str) -> str:
        """Try to extract what the user said as a device kind (for error messages)."""
        # Remove common verbs and prepositions to leave the noun
        stop_words = {
            "увімкн",
            "вимкн",
            "перемкн",
            "будь ласка",
            "будь-ласка",
            "у",
            "в",
            "на",
            "біля",
            "мені",
        }
        tokens = lower.split()
        remainder = [t for t in tokens if t not in stop_words and len(t) > 2]
        return " ".join(remainder[:3]) if remainder else lower[:30]
