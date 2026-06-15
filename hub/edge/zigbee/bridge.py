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

Mapping is **by payload field**, not by the device name — one combo sensor (e.g.
mmWave presence + temperature + humidity + illuminance in a single message) fans
out into several project topics at once, mirroring the mock sensors:
  * climate fields (temperature/humidity/illuminance/…) → ``home/{slug}/sensors`` tier 1
  * occupancy|presence (rising edge only)               → ``home/{slug}/alert``   tier 2 (motion)
  * contact                                             → ``home/{slug}/alert``   tier 2 (door_open/close)
  * power/voltage/current/energy                        → ``home/{slug}/sensors`` tier 0
  * nothing recognised                                  → ``home/{slug}/sensors`` tier 1 (numeric pass-through)

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


# Climate fields → home/{slug}/sensors, tier 1 (numeric environmental readings).
_CLIMATE_FIELDS = (
    "temperature",
    "humidity",
    "pressure",
    "co2",
    "voc",
    "pm25",
    "illuminance",
    "illuminance_lux",
)

# Smart-plug / energy-meter fields → home/{slug}/sensors, tier 0 (matches PowerSensor).
_POWER_RENAME = {
    "power": "power_w",
    "voltage": "voltage_v",
    "current": "current_a",
    "energy": "energy_kwh",
}

# Rising-edge state for presence/occupancy, keyed by device_id. mmWave/PIR combo
# sensors republish their full state every cycle (with presence unchanged), so we
# emit a motion *alert* only on the False→True transition — otherwise the events
# feed gets one "motion" per heartbeat the whole time a room is occupied.
_presence_state: dict[str, bool] = {}


def translate(slug: str, kind: str, src: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Fan one Z2M device message out into ``[(subtopic, project_payload), ...]``.

    Routing is by **payload fields**, not by the device's ``kind`` — a single combo
    sensor (e.g. mmWave presence + temperature + humidity + illuminance in one
    message) maps to *several* project topics at once: climate → ``sensors`` tier 1,
    presence → ``alert`` tier 2, etc. ``kind`` is kept only as a label in
    ``device_id``. Each ``subtopic`` is appended to ``home/{slug}/``.
    """
    device_id = f"zigbee-{slug}-{kind}"
    parts: list[tuple[str, int, dict[str, Any]]] = []

    climate = {k: src[k] for k in _CLIMATE_FIELDS if k in src}
    if climate:
        parts.append(("sensors", 1, climate))

    power = {dst: src[srck] for srck, dst in _POWER_RENAME.items() if srck in src}
    if power:
        if "state" in src:  # ON/OFF relay state of the plug
            power["state"] = src["state"]
        parts.append(("sensors", 0, power))

    if "contact" in src:
        # Zigbee convention: contact=true = magnets together (closed).
        alert_type = "door_close" if src["contact"] else "door_open"
        parts.append(("alert", 2, {"alert_type": alert_type, "confidence": 1.0}))

    # Presence/occupancy, edge-triggered. Devices vary on the field name
    # (occupancy on PIR, presence on mmWave) — accept either.
    motion = src.get("occupancy")
    if motion is None:
        motion = src.get("presence")
    if isinstance(motion, bool):
        prev = _presence_state.get(device_id, False)
        _presence_state[device_id] = motion
        if motion and not prev:  # rising edge only
            parts.append(("alert", 2, {"alert_type": "motion", "confidence": 1.0}))

    # Nothing recognised → pass numeric/bool fields through as a tier-1 sensor,
    # so an unsupported device still surfaces *something* in the feed.
    if not parts:
        generic = {
            k: v
            for k, v in src.items()
            if isinstance(v, int | float | bool) and k not in _DIAG_FIELDS
        }
        if generic:
            parts.append(("sensors", 1, generic))

    ts = datetime.now(UTC).isoformat()
    results: list[tuple[str, dict[str, Any]]] = []
    for subtopic, tier, fields in parts:
        payload: dict[str, Any] = {
            "device_id": device_id,
            "ts": ts,
            "tier": tier,
            **fields,
        }
        for diag in _DIAG_FIELDS:
            if diag in src:
                payload[diag] = src[diag]
        results.append((subtopic, payload))
    return results


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

    for subtopic, payload in translate(slug, kind, src):
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
