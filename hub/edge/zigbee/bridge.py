"""Zigbee2MQTT → ``home/{slug}/...`` schema bridge.

Zigbee2MQTT (Z2M) talks to the USB coordinator (SONOFF/EFR32MG24 "Dongle-PMG24",
driver ``ember``) and publishes each device's state on ``zigbee2mqtt/{friendly_name}``
using *its own* JSON schema and with **no data-tier tag**. The backend's
``mqtt_subscriber`` doesn't understand that — it expects the project schema
(``home/{slug}/sensors`` / ``home/{slug}/alert`` with a ``tier`` field, see the
mock sensors in ``mock_sensors/``).

This bridge closes the gap. We adopt the convention that every Z2M device's
**friendly name = ``"{slug}/{kind}"``** (set once in the Z2M frontend), e.g.
``living_room/temp`` → topic ``zigbee2mqtt/living_room/temp``. The bridge parses
the room ``slug`` and device ``kind`` straight from the topic, translates the Z2M
payload into the project schema with the correct tier, and republishes — so the
backend persists Zigbee readings exactly like an ESP32 or mock sensor. **No
backend change is required.**

Mapping (kind prefix → project topic + tier), mirroring the mock sensors:
  * temp/climate/air  → ``home/{slug}/sensors``  tier 1  (temperature, humidity, …)
  * motion/pir/presence → ``home/{slug}/alert``  tier 2  (alert_type=motion)
  * contact/door/window → ``home/{slug}/alert``  tier 2  (door_open/door_close)
  * plug/power/meter  → ``home/{slug}/sensors``  tier 0  (power_w, voltage_v, …)
  * anything else     → ``home/{slug}/sensors``  tier 1  (numeric pass-through)

``battery`` and ``linkquality`` are attached as diagnostic fields to every
published payload so device health is visible in the events feed without a
dedicated topic.

Run: ``python -m hub.edge.zigbee.bridge``
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

import aiomqtt

logger = logging.getLogger(__name__)

MQTT_HOST = os.getenv("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
# Z2M base topic (Z2M setting `mqtt.base_topic`, default "zigbee2mqtt").
Z2M_BASE = os.getenv("Z2M_BASE_TOPIC", "zigbee2mqtt")

# Diagnostic fields carried through on every publish, if present in the Z2M payload.
_DIAG_FIELDS = ("battery", "linkquality")


def _climate(src: dict[str, Any]) -> tuple[str, int, dict[str, Any]] | None:
    """Temperature / humidity / air-quality sensor → home/{slug}/sensors, tier 1."""
    keep = (
        "temperature",
        "humidity",
        "pressure",
        "co2",
        "voc",
        "pm25",
        "illuminance_lux",
    )
    fields = {k: src[k] for k in keep if k in src}
    if not fields:
        return None
    return "sensors", 1, fields


def _motion(src: dict[str, Any]) -> tuple[str, int, dict[str, Any]] | None:
    """PIR / occupancy → home/{slug}/alert, tier 2. Only the *detected* edge is published."""
    occ = src.get("occupancy")
    if not occ:  # None or False — mirror the mock PIR which only emits on motion
        return None
    return "alert", 2, {"alert_type": "motion", "confidence": 1.0}


def _contact(src: dict[str, Any]) -> tuple[str, int, dict[str, Any]] | None:
    """Door/window reed → home/{slug}/alert, tier 2.

    Zigbee convention: ``contact: true`` = magnets together (closed),
    ``contact: false`` = separated (open).
    """
    contact = src.get("contact")
    if contact is None:
        return None
    alert_type = "door_close" if contact else "door_open"
    return "alert", 2, {"alert_type": alert_type, "confidence": 1.0}


def _power(src: dict[str, Any]) -> tuple[str, int, dict[str, Any]] | None:
    """Smart plug / energy meter → home/{slug}/sensors, tier 0 (matches PowerSensor)."""
    rename = {
        "power": "power_w",
        "voltage": "voltage_v",
        "current": "current_a",
        "energy": "energy_kwh",
    }
    fields = {dst: src[srck] for srck, dst in rename.items() if srck in src}
    if "state" in src:  # ON/OFF relay state of the plug
        fields["state"] = src["state"]
    if not fields:
        return None
    return "sensors", 0, fields


def _generic(src: dict[str, Any]) -> tuple[str, int, dict[str, Any]] | None:
    """Unknown kind → pass numeric fields through to home/{slug}/sensors, tier 1."""
    fields = {
        k: v for k, v in src.items() if isinstance(v, int | float | bool) and k not in _DIAG_FIELDS
    }
    if not fields:
        return None
    return "sensors", 1, fields


# Matched by ``kind.startswith(prefix)`` for any prefix in the tuple.
_HANDLERS: list[tuple[tuple[str, ...], Any]] = [
    (("temp", "th", "clim", "air", "co2"), _climate),
    (("motion", "pir", "occup", "presence"), _motion),
    (("contact", "door", "window", "reed"), _contact),
    (("plug", "power", "meter", "socket", "outlet", "energy"), _power),
]


def translate(slug: str, kind: str, src: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Map one Z2M device message to ``(subtopic, project_payload)`` or ``None`` to drop.

    ``subtopic`` is appended to ``home/{slug}/`` (e.g. ``"sensors"`` → ``home/living_room/sensors``).
    """
    handler = _generic
    kl = kind.lower()
    for prefixes, fn in _HANDLERS:
        if kl.startswith(prefixes):
            handler = fn
            break

    result = handler(src)
    if result is None:
        return None
    subtopic, tier, fields = result

    payload: dict[str, Any] = {
        "device_id": f"zigbee-{slug}-{kind}",
        "ts": datetime.now(UTC).isoformat(),
        "tier": tier,
        **fields,
    }
    for diag in _DIAG_FIELDS:
        if diag in src:
            payload[diag] = src[diag]
    return subtopic, payload


async def _handle(message: aiomqtt.Message, out: aiomqtt.Client) -> None:
    topic = str(message.topic)
    parts = topic.split("/")
    # Expect exactly: {base}/{slug}/{kind}. Z2M bridge events ({base}/bridge/*)
    # and sub-topics ({base}/{fn}/availability, /set, /get) are longer or carry
    # a reserved first segment — ignore them.
    if len(parts) != 3 or parts[0] != Z2M_BASE:
        return
    slug, kind = parts[1], parts[2]
    if slug == "bridge":
        return

    raw = message.payload if isinstance(message.payload, str | bytes) else b""
    try:
        src = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.debug("Z2M %s: non-JSON payload, skipping", topic)
        return
    if not isinstance(src, dict):
        return

    mapped = translate(slug, kind, src)
    if mapped is None:
        return
    subtopic, payload = mapped
    out_topic = f"home/{slug}/{subtopic}"
    await out.publish(out_topic, json.dumps(payload), qos=1)
    logger.info("Zigbee %s → %s (tier %s)", topic, out_topic, payload["tier"])


async def run() -> None:
    sub_topic = f"{Z2M_BASE}/+/+"
    while True:
        try:
            async with aiomqtt.Client(MQTT_HOST, MQTT_PORT) as client:
                await client.subscribe(sub_topic)
                logger.info(
                    "Zigbee bridge up: %s → home/{slug}/... @ %s:%s",
                    sub_topic,
                    MQTT_HOST,
                    MQTT_PORT,
                )
                async for message in client.messages:
                    await _handle(message, client)
        except aiomqtt.MqttError as exc:
            logger.warning("Zigbee bridge MQTT lost: %s — reconnecting in 5s", exc)
            await asyncio.sleep(5)
        except Exception as exc:  # noqa: BLE001
            logger.error("Zigbee bridge crashed: %s — restarting in 5s", exc, exc_info=True)
            await asyncio.sleep(5)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
