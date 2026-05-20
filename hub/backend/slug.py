"""Room MQTT-slug generation.

Each Room carries a stable ASCII ``slug`` used as its MQTT/Redis identity:
topic ``home/{slug}/...`` and Redis channel ``cv:detections:{slug}``. The edge
CV/voice services publish under this slug via their ``ROOM`` env var, and the
CV WebSocket subscribes by it — see ``hub.backend.routes.cv.ws_cv``.

Slugs are generated from the human room name (Cyrillic → Latin transliteration)
so binding a room to a camera needs no manual MQTT-name step.
"""

from __future__ import annotations

# Ukrainian Cyrillic → Latin (simplified BGN/PCGN). Lowercase keys only.
_TRANSLIT: dict[str, str] = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "h",
    "ґ": "g",
    "д": "d",
    "е": "e",
    "є": "ie",
    "ж": "zh",
    "з": "z",
    "и": "y",
    "і": "i",
    "ї": "i",
    "й": "i",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ь": "",
    "ю": "iu",
    "я": "ia",
}


def _slugify(name: str) -> str:
    """Transliterate and normalise a room name to a bare ASCII slug."""
    chars: list[str] = []
    for ch in name.strip().lower():
        if ch in _TRANSLIT:
            chars.append(_TRANSLIT[ch])
        elif ch.isascii() and ch.isalnum():
            chars.append(ch)
        else:
            chars.append("_")
    slug = "".join(chars)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_")[:60] or "room"


def slugify_room(name: str, taken: set[str]) -> str:
    """Return a unique slug for *name*, avoiding any value already in *taken*.

    The chosen slug is added to *taken* so repeated calls within one batch
    (e.g. a floor-plan replace) stay collision-free.
    """
    base = _slugify(name)
    slug = base
    n = 2
    while slug in taken:
        slug = f"{base}_{n}"
        n += 1
    taken.add(slug)
    return slug
