"""Single source of truth for intent vocabulary + MASSIVE mapping.

Edge runtime reads ``INTENT_LABELS`` via ``models/intent_classifier/metadata.json``
written by ``train.py``; do not hard-code labels on the edge side.
"""

from __future__ import annotations

# Canonical intent vocabulary — must match what hub/edge orchestrator routes on.
# Order is significant for the ONNX classifier head (label index → string).
INTENT_LABELS: list[str] = [
    "light_on",
    "light_off",
    "light_toggle",
    "light_brightness_set",
    "light_color_set",
    "relay_on",
    "relay_off",
    "door_open",
    "door_close",
    "thermostat_set",
    "query_temperature",
    "query_humidity",
    "query_state",
    "summarize_events",
    "scene_generic",
    "ask_clarification",
]

# Mapping from MASSIVE dataset intent labels to our canonical set.
# Source: arXiv 2204.08582 §3.  Values not listed are dropped during prep.
MASSIVE_INTENT_MAP: dict[str, str] = {
    # iot scenario — direct device control
    "iot_hue_lighton": "light_on",
    "iot_hue_lightoff": "light_off",
    "iot_hue_lightup": "light_brightness_set",
    "iot_hue_lightdim": "light_brightness_set",
    "iot_hue_lightchange": "light_color_set",
    "iot_wemo_on": "relay_on",
    "iot_wemo_off": "relay_off",
    "iot_cleaning": "scene_generic",
    "iot_coffee": "scene_generic",
    # weather / general queries (we treat them as state queries)
    "weather_query": "query_temperature",
    "general_quirky": "query_state",  # broad: many "what is X" utterances
    # recommendation scenario — used for scenes / multi-device commands
    "recommendation_locations": "scene_generic",
}

# Intents that are queries vs. control — drives routing in orchestrator.
QUERY_INTENTS: frozenset[str] = frozenset(
    {"query_temperature", "query_humidity", "query_state", "summarize_events"}
)
CONTROL_INTENTS: frozenset[str] = frozenset(
    {
        "light_on",
        "light_off",
        "light_toggle",
        "light_brightness_set",
        "light_color_set",
        "relay_on",
        "relay_off",
        "door_open",
        "door_close",
        "thermostat_set",
    }
)
SCENE_INTENTS: frozenset[str] = frozenset({"scene_generic"})


def label_to_index(label: str) -> int:
    """Return ONNX head index for a label; raises if unknown."""
    return INTENT_LABELS.index(label)


def index_to_label(idx: int) -> str:
    """Return the canonical intent for an ONNX head index."""
    return INTENT_LABELS[idx]
