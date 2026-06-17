#!/usr/bin/env python3
"""
Run all mock IoT sensors and publish to the real hub on RPi via mTLS (port 8883).

Setup (once):
    bash mock_sensors/setup_certs.sh

Run:
    make mock-sensors
    # or:
    uv run python mock_sensors/run_all.py

Env overrides:
    MQTT_HOST=192.168.0.x  INTERVAL_SCALE=0.1  uv run python mock_sensors/run_all.py
"""

import asyncio
import json
import logging
import ssl
import sys
from pathlib import Path

import aiomqtt

sys.path.insert(0, str(Path(__file__).parent))

from base import Actuator
from config import (
    INTERVAL_SCALE,
    MQTT_HOST,
    MQTT_PORT,
    MQTT_TLS_CA,
    MQTT_TLS_CERT,
    MQTT_TLS_KEY,
    ROOMS,
)
from sensors.air_quality import AirQualitySensor
from sensors.camera import CameraEventSensor
from sensors.door import DoorSensor
from sensors.light import LightActuator
from sensors.motion import MotionSensor
from sensors.power import PowerSensor
from sensors.temperature import TempHumiditySensor
from sensors.thermostat import ThermostatActuator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _tls_context() -> ssl.SSLContext | None:
    """Build an SSL context for mTLS if certs exist, otherwise return None."""
    ca = Path(MQTT_TLS_CA)
    cert = Path(MQTT_TLS_CERT)
    key = Path(MQTT_TLS_KEY)

    if MQTT_PORT != 8883:
        return None

    if not ca.exists():
        log.error(
            "CA cert not found at %s\n" "Run:  bash mock_sensors/setup_certs.sh",
            ca,
        )
        sys.exit(1)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False  # self-signed cert has CN=mosquitto, not the IP
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cafile=str(ca))

    if cert.exists() and key.exists():
        ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
        log.info("TLS: mTLS with client cert %s", cert.name)
    else:
        log.warning("Client cert not found — broker requires it. Run setup_certs.sh")
        sys.exit(1)

    return ctx


def _build_sensors() -> list:
    sensors = []

    for room in ROOMS:
        sensors.append(TempHumiditySensor(room))

    for room in ["living_room", "kitchen"]:
        sensors.append(AirQualitySensor(room))

    for room in ["living_room", "bedroom"]:
        sensors.append(MotionSensor(room))

    for room in ["living_room", "kitchen"]:
        sensors.append(PowerSensor(room))

    sensors.append(DoorSensor("bedroom", "entrance"))
    sensors.append(DoorSensor("kitchen", "balcony"))
    sensors.append(DoorSensor("living_room", "front"))

    for room in ROOMS:
        sensors.append(CameraEventSensor(room))

    return sensors


def _build_actuators() -> list[Actuator]:
    """Controllable devices that listen for commands (UI sliders / scenes / voice).

    Add a matching DevicePlacement in the floor-plan editor with the same
    ``device_id`` (e.g. ``mock-light-living_room``) to drive these from the UI.
    """
    return [
        LightActuator("living_room"),
        LightActuator("bedroom"),
        ThermostatActuator("living_room"),
        ThermostatActuator("bedroom"),
    ]


async def _dispatch_commands(client: aiomqtt.Client, actuators: list[Actuator]) -> None:
    """Single consumer of incoming MQTT messages — routes commands to actuators.

    aiomqtt exposes one shared ``client.messages`` iterator, so all command
    handling funnels through here while the publish loops run independently.
    """
    routes: dict[str, Actuator] = {}
    for a in actuators:
        for topic in a.command_topics:
            routes[topic] = a

    async for message in client.messages:
        actuator = routes.get(str(message.topic))
        if actuator is None:
            continue
        raw = message.payload
        try:
            payload = json.loads(raw if isinstance(raw, str | bytes | bytearray) else b"{}")
        except (json.JSONDecodeError, ValueError) as exc:
            log.warning("[%s] bad command payload: %s", actuator.device_id, exc)
            continue
        if not isinstance(payload, dict):
            continue
        try:
            await actuator.handle_command(client, payload)
        except Exception as exc:  # noqa: BLE001 - keep dispatcher alive
            log.error("[%s] command failed: %s", actuator.device_id, exc)


async def _safe_loop(sensor, client: aiomqtt.Client) -> None:
    while True:
        try:
            await sensor.loop(client)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("[%s] crashed: %s — restarting in 10s", sensor.device_id, exc)
            await asyncio.sleep(10)


async def main() -> None:
    sensors = _build_sensors()
    actuators = _build_actuators()
    tls = _tls_context()

    log.info(
        "Connecting to %s:%d  |  %d sensors + %d actuators  |  TLS=%s  |  INTERVAL_SCALE=%.2f",
        MQTT_HOST,
        MQTT_PORT,
        len(sensors),
        len(actuators),
        tls is not None,
        INTERVAL_SCALE,
    )

    while True:
        try:
            async with aiomqtt.Client(MQTT_HOST, MQTT_PORT, tls_context=tls) as client:
                for a in actuators:
                    for topic in a.command_topics:
                        await client.subscribe(topic, qos=1)
                log.info("Connected. Publishing + listening for commands… (Ctrl+C to stop)")
                loops = [_safe_loop(s, client) for s in (*sensors, *actuators)]
                await asyncio.gather(*loops, _dispatch_commands(client, actuators))
        except aiomqtt.MqttError as exc:
            log.warning("MQTT disconnected: %s — retrying in 5s", exc)
            await asyncio.sleep(5)
        except KeyboardInterrupt:
            log.info("Stopped.")
            return


if __name__ == "__main__":
    asyncio.run(main())
