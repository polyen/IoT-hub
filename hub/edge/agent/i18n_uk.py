"""Centralised Ukrainian response templates for the voice agent.

All user-facing strings live here. Import constants and call render_* helpers;
never inline Ukrainian strings in orchestrator / resolver logic.

Designed for future multi-language expansion: add `i18n_en.py` with the same
API and swap the import in orchestrator; current scope is UA-only.
"""

from __future__ import annotations

from enum import StrEnum
from string import Template
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Enums (mirrored from text_resolver to avoid circular import)
# ---------------------------------------------------------------------------


class ResolutionFailureKind(StrEnum):
    UNCLEAR_INTENT = "unclear_intent"
    UNKNOWN_DEVICE_KIND = "unknown_device_kind"
    DEVICE_NOT_FOUND = "device_not_found"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED_ACTION = "unsupported_action"


# ---------------------------------------------------------------------------
# Action ↔ Ukrainian
# ---------------------------------------------------------------------------

ACTION_UA: dict[str, str] = {
    "on": "увімкнути",
    "off": "вимкнути",
    "toggle": "перемкнути",
    "open": "відкрити",
    "close": "закрити",
    "set": "встановити",
    "inc": "збільшити",
    "dec": "зменшити",
    "brightness_set": "встановити яскравість",
    "temp_set": "встановити температуру",
}

ACTION_PAST_UA: dict[str, str] = {
    "on": "увімкнено",
    "off": "вимкнено",
    "toggle": "перемкнено",
    "open": "відкрито",
    "close": "закрито",
    "set": "встановлено",
    "inc": "збільшено",
    "dec": "зменшено",
    "brightness_set": "яскравість встановлено",
    "temp_set": "температуру встановлено",
}

# ---------------------------------------------------------------------------
# Device-kind ↔ Ukrainian nominative singular
# ---------------------------------------------------------------------------

KIND_UA: dict[str, str] = {
    "light": "освітлення",
    "relay": "реле",
    "lock": "замок",
    "thermostat": "термостат",
    "camera": "камера",
    "speaker": "динамік",
    "sensor_pir": "датчик руху",
    "sensor_door": "датчик дверей",
    "sensor_dht": "датчик температури",
    "sensor_mq2": "датчик газу",
    "sensor_power": "лічильник потужності",
}

# ---------------------------------------------------------------------------
# Failure templates  (key = ResolutionFailureKind value)
# ---------------------------------------------------------------------------

FAILURE_TEMPLATES: dict[str, str] = {
    ResolutionFailureKind.UNCLEAR_INTENT: (
        "Я не до кінця зрозумів, що зробити. "
        "Скажіть простіше: «увімкни/вимкни ‹пристрій› у ‹кімнаті›»."
    ),
    ResolutionFailureKind.UNKNOWN_DEVICE_KIND: (
        "У мене немає такого типу пристрою як «$kind_text». " "Я вмію керувати: $known_kinds_ua."
    ),
    ResolutionFailureKind.DEVICE_NOT_FOUND: (
        "У мене не зареєстровано «$kind_ua»$room_part. " "Додайте пристрій на сторінці «Пристрої»."
    ),
    ResolutionFailureKind.AMBIGUOUS: ("У $room_ua кілька пристроїв: $candidate_labels. Який саме?"),
    ResolutionFailureKind.UNSUPPORTED_ACTION: (
        "Пристрій «$device_label» не підтримує дію «$action_ua». "
        "Доступно: $available_actions_ua."
    ),
}

# ---------------------------------------------------------------------------
# Success templates  (key = (kind, action))
# Fallback: "${device_label_or_kind} ${action_past}." is used when no entry.
# ---------------------------------------------------------------------------

SUCCESS_TEMPLATES: dict[tuple[str, str], str] = {
    ("light", "on"): "Світло$room_part увімкнено.",
    ("light", "off"): "Світло$room_part вимкнено.",
    ("light", "toggle"): "Світло$room_part перемкнено.",
    ("light", "brightness_set"): "Яскравість$room_part встановлено на $value%.",
    ("relay", "on"): "$device_label увімкнено.",
    ("relay", "off"): "$device_label вимкнено.",
    ("relay", "toggle"): "$device_label перемкнено.",
    ("lock", "open"): "Замок$room_part відчинено.",
    ("lock", "close"): "Замок$room_part зачинено.",
    ("thermostat", "temp_set"): "Температуру$room_part встановлено на $value°C.",
    ("thermostat", "inc"): "Температуру$room_part підвищено.",
    ("thermostat", "dec"): "Температуру$room_part знижено.",
}

# Broadcast success
BROADCAST_SUCCESS_TEMPLATE = "Всі пристрої$room_part $action_past."


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def render_failure(resolution: Any) -> str:
    """Return a user-facing Ukrainian string for a failed resolution."""
    fk = resolution.failure_kind
    if fk is None:
        return "Не вдалося виконати команду."

    template_str = FAILURE_TEMPLATES.get(str(fk), "Не вдалося виконати команду.")
    ctx: dict[str, str] = resolution.failure_context or {}
    try:
        return Template(template_str).safe_substitute(ctx)
    except Exception:
        return template_str


def render_success(resolution: Any, broadcast: bool = False) -> str:
    """Return a user-facing Ukrainian confirmation string for a successful resolution."""
    action = resolution.action or ""
    device = resolution.device
    params = resolution.params or {}

    room_part = ""
    if device and device.room_name_ua:
        room_part = f" у {device.room_name_ua.lower()}"

    if broadcast:
        action_past = ACTION_PAST_UA.get(action, action)
        return Template(BROADCAST_SUCCESS_TEMPLATE).safe_substitute(
            room_part=room_part,
            action_past=action_past,
        )

    if device is None:
        return "Виконано."

    kind = device.kind
    tpl_str = SUCCESS_TEMPLATES.get((kind, action))

    if tpl_str:
        label = device.label or KIND_UA.get(kind, kind)
        value = str(params.get("value", ""))
        action_ua = ACTION_UA.get(action, action)
        action_past = ACTION_PAST_UA.get(action, action)
        return Template(tpl_str).safe_substitute(
            room_part=room_part,
            device_label=label,
            value=value,
            action_ua=action_ua,
            action_past=action_past,
        )

    # Generic fallback
    label = device.label or KIND_UA.get(kind, kind)
    action_past = ACTION_PAST_UA.get(action, action)
    return f"{label.capitalize()} {action_past}."


def known_kinds_ua(kinds: list[str]) -> str:
    """Format a list of device kind strings as comma-separated Ukrainian."""
    parts = [KIND_UA.get(k, k) for k in kinds]
    return ", ".join(parts) if parts else "пристрої"
