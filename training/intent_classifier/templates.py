"""Synthetic-augmentation templates for Ukrainian smart-home commands.

Each template is a tuple (intent, *slot_lists, format_string) where the format
string contains placeholders that get expanded with the Cartesian product of
slot_lists.  Used by prepare_dataset.py to top up rare classes from MASSIVE.

Designed to cover orthographic variations seen in real prod logs:
- standard verbs: увімкни, вимкни
- variations: ввімкни, активуй, запали, гори, погасі
- locative case: у вітальні, на кухні, в дитячій
- typos / russisms that voice-STT introduces: гореть, светло
"""

from __future__ import annotations

# Verb stems (with optional politeness prefix dropped by the model)
VERBS_ON: list[str] = [
    "увімкни",
    "ввімкни",
    "вмикай",
    "активуй",
    "запали",
    "запусти",
    "запали-но",
    "будь ласка увімкни",
    # imperative of "горіти" (to be on/burning) — common colloquial form
    "гори",
    # Russism: "гореть" = горіти — heard frequently in STT output
    "гореть",
    # full imperatives
    "засвіти",
    "освіти",
]

VERBS_OFF: list[str] = [
    "вимкни",
    "вимикай",
    "погасі",
    "загаси",
    "погаси",
    "вимкни-но",
    "деактивуй",
    "припини",
    "будь ласка вимкни",
    # common Ukrainian off-verbs not in the original list
    "гаси",
    "заглуши",
    "відключи",
    "знеструмуй",
]

VERBS_TOGGLE: list[str] = ["перемкни", "переключи", "перевімкни"]

VERBS_OPEN: list[str] = ["відчини", "відкрий", "розкрий"]

VERBS_CLOSE: list[str] = ["зачини", "закрий"]

# Locative-form room phrases (matches what users actually say)
ROOMS_LOCATIVE: list[str] = [
    "у вітальні",
    "на кухні",
    "у спальні",
    "у дитячій",
    "в коридорі",
    "у ванній",
    "на балконі",
    "в кабінеті",
    "у залі",
    "",  # often the user omits the room entirely
]

# Device nouns in their accusative (the verbs take accusative object)
LIGHT_NOUNS: list[str] = [
    "світло",
    "лампу",
    "люстру",
    "освітлення",
    "ліхтар",
    "торшер",
    # additional light types seen in prod logs
    "бра",
    "нічник",
    "підсвітку",
    "прожектор",
    "світильник",
]

RELAY_NOUNS: list[str] = [
    "розетку",
    "вентилятор",
    "обігрівач",
    "реле",
    "пилосос",
]

DOOR_NOUNS: list[str] = ["двері", "ворота", "браму"]

# Numeric values for brightness / temperature
BRIGHTNESS_VALUES: list[int] = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
TEMP_VALUES: list[int] = [16, 18, 19, 20, 21, 22, 23, 24, 25, 26]
COLORS: list[str] = ["теплим", "холодним", "білим", "червоним", "синім", "зеленим"]

# Query phrases
QUERY_TEMP_TEMPLATES: list[str] = [
    "яка температура {room}",
    "скільки градусів {room}",
    "температура {room}",
    "як з температурою {room}",
]

QUERY_HUMIDITY_TEMPLATES: list[str] = [
    "яка вологість {room}",
    "скільки відсотків вологості {room}",
    "вологість {room}",
]

QUERY_STATE_TEMPLATES: list[str] = [
    "що {room} зараз",
    "як стан {room}",
    "покажи стан будинку",
    "статус {room}",
    "що з {room}",
]

SUMMARY_TEMPLATES: list[str] = [
    # today
    "що сталось сьогодні",
    "розкажи події дня",
    "підсумок дня",
    "звіт за сьогодні",
    "що відбулось за сьогодні",
    # yesterday
    "що сталось вчора",
    "розкажи що було вчора",
    "підсумок вчорашнього дня",
    "що відбувалось вчора",
    "події вчора",
    "вчорашній звіт",
    # this week / last week
    "підсумок тижня",
    "розкажи події за тиждень",
    "що сталось цього тижня",
    "тижневий звіт",
    "що відбувалось за тиждень",
    # night
    "що відбувалось вночі",
    "нічний звіт",
    "що сталось вночі",
    # general summary keywords
    "дай підсумок",
    "зроби звіт",
    "розкажи що сталось",
    "що взагалі відбувалось",
    "які події за останні години",
    "розкажи події за останній час",
]

# Scene phrases — these are open-ended and matched by both intent classifier
# AND scene_engine alias lookup
SCENE_TEMPLATES: list[str] = [
    "режим кіно",
    "режим ночі",
    "ввімкни режим кіно",
    "активуй романтику",
    "режим читання",
    "вмикай вечір",
    "час спати",
    "ранковий режим",
    # single-word scene triggers (common in prod)
    "кіноперегляд",
    "кіно",
    "фільм",
    "романтика",
    "дискотека",
    "вечірка",
    "прибирання",
    "сон",
    "відпочинок",
    # verb + scene
    "активуй кіно",
    "ввімкни вечірній режим",
    "режим роботи",
    "ранковий підйом",
    "нічний режим",
    "вмикай кіно",
    "запусти режим релаксу",
]


def expand_template(template: str, **substitutions: str) -> str:
    """Lower-case + strip + collapse whitespace after substitution."""
    text = template.format(**substitutions).lower().strip()
    return " ".join(text.split())


# Standalone "let it be on" phrases — different syntactic structure than verb+noun+room
_HAI_GORYT_ROOMS: list[str] = [
    "вітальня",
    "кухня",
    "спальня",
    "дитяча",
    "коридор",
    "ванна",
    "балкон",
    "кабінет",
    "зал",
]


def generate_light_on() -> list[str]:
    out: list[str] = []
    for verb in VERBS_ON:
        for noun in LIGHT_NOUNS:
            for room in ROOMS_LOCATIVE:
                out.append(expand_template("{verb} {noun} {room}", verb=verb, noun=noun, room=room))
    # "хай горить {room_nominative}" / "нехай горить {noun} {room}"
    for room in _HAI_GORYT_ROOMS:
        out.append(f"хай горить {room}")
        out.append(f"нехай горить {room}")
        out.append(f"хай горить світло {room}")
    # Russism "гореть" as standalone: "гореть у вітальні"
    for room in ROOMS_LOCATIVE:
        if room:
            out.append(f"гореть {room}")
            out.append(f"горить {room}")
            out.append(f"хай {room} горить")
    return out


def generate_light_off() -> list[str]:
    out: list[str] = []
    for verb in VERBS_OFF:
        for noun in LIGHT_NOUNS:
            for room in ROOMS_LOCATIVE:
                out.append(expand_template("{verb} {noun} {room}", verb=verb, noun=noun, room=room))
    return out


def generate_light_toggle() -> list[str]:
    return [
        expand_template("{verb} {noun} {room}", verb=v, noun=n, room=r)
        for v in VERBS_TOGGLE
        for n in LIGHT_NOUNS
        for r in ROOMS_LOCATIVE
    ]


def generate_light_brightness_set() -> list[str]:
    return [
        expand_template("встанови {noun} на {value} відсотків {room}", noun=n, value=str(v), room=r)
        for n in LIGHT_NOUNS
        for v in BRIGHTNESS_VALUES
        for r in ROOMS_LOCATIVE
    ]


def generate_light_color_set() -> list[str]:
    return [
        expand_template("зроби {noun} {color} {room}", noun=n, color=c, room=r)
        for n in LIGHT_NOUNS
        for c in COLORS
        for r in ROOMS_LOCATIVE
    ]


def generate_relay_on() -> list[str]:
    return [
        expand_template("{verb} {noun} {room}", verb=v, noun=n, room=r)
        for v in VERBS_ON
        for n in RELAY_NOUNS
        for r in ROOMS_LOCATIVE
    ]


def generate_relay_off() -> list[str]:
    return [
        expand_template("{verb} {noun} {room}", verb=v, noun=n, room=r)
        for v in VERBS_OFF
        for n in RELAY_NOUNS
        for r in ROOMS_LOCATIVE
    ]


def generate_door_open() -> list[str]:
    return [
        expand_template("{verb} {noun} {room}", verb=v, noun=n, room=r)
        for v in VERBS_OPEN
        for n in DOOR_NOUNS
        for r in ROOMS_LOCATIVE
    ]


def generate_door_close() -> list[str]:
    return [
        expand_template("{verb} {noun} {room}", verb=v, noun=n, room=r)
        for v in VERBS_CLOSE
        for n in DOOR_NOUNS
        for r in ROOMS_LOCATIVE
    ]


THERMOSTAT_COLLOQUIAL: list[str] = [
    # direction without numeric value — the model should still predict thermostat_set
    "зроби тепліше",
    "зроби прохолодніше",
    "зроби холодніше",
    "трохи тепліше",
    "трохи холодніше",
    "підвищ температуру",
    "знижи температуру",
    "збільш нагрів",
    "зменш нагрів",
    "тепліше",
    "холодніше",
    "прохолодніше",
    # room-qualified
    "зроби тепліше у вітальні",
    "зроби прохолодніше у спальні",
    "підвищ температуру на кухні",
    "знижи температуру у дитячій",
    "натопи кімнату",
    "охолоди кімнату",
]


def generate_thermostat_set() -> list[str]:
    out: list[str] = []
    for v in TEMP_VALUES:
        for room in ROOMS_LOCATIVE:
            out.append(
                expand_template(
                    "встанови температуру {value} градусів {room}", value=str(v), room=room
                )
            )
            out.append(
                expand_template("нагрій {room} до {value} градусів", value=str(v), room=room)
            )
            out.append(expand_template("поставь {value} градусів {room}", value=str(v), room=room))
    # Include colloquial direction phrases
    out.extend(THERMOSTAT_COLLOQUIAL)
    return out


def generate_query_temperature() -> list[str]:
    return [expand_template(t, room=r) for t in QUERY_TEMP_TEMPLATES for r in ROOMS_LOCATIVE]


def generate_query_humidity() -> list[str]:
    return [expand_template(t, room=r) for t in QUERY_HUMIDITY_TEMPLATES for r in ROOMS_LOCATIVE]


def generate_query_state() -> list[str]:
    return [expand_template(t, room=r) for t in QUERY_STATE_TEMPLATES for r in ROOMS_LOCATIVE]


def generate_summarize_events() -> list[str]:
    return [expand_template(t) for t in SUMMARY_TEMPLATES]


def generate_scene_generic() -> list[str]:
    return [expand_template(t) for t in SCENE_TEMPLATES]


# Public API — maps each intent label to its generator function.
GENERATORS: dict[str, callable] = {  # type: ignore[type-arg]
    "light_on": generate_light_on,
    "light_off": generate_light_off,
    "light_toggle": generate_light_toggle,
    "light_brightness_set": generate_light_brightness_set,
    "light_color_set": generate_light_color_set,
    "relay_on": generate_relay_on,
    "relay_off": generate_relay_off,
    "door_open": generate_door_open,
    "door_close": generate_door_close,
    "thermostat_set": generate_thermostat_set,
    "query_temperature": generate_query_temperature,
    "query_humidity": generate_query_humidity,
    "query_state": generate_query_state,
    "summarize_events": generate_summarize_events,
    "scene_generic": generate_scene_generic,
}
