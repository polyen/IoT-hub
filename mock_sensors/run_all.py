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
import logging
import ssl
import sys
from pathlib import Path

import aiomqtt

sys.path.insert(0, str(Path(__file__).parent))

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
from sensors.motion import MotionSensor
from sensors.power import PowerSensor
from sensors.temperature import TempHumiditySensor

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
    tls = _tls_context()

    log.info(
        "Connecting to %s:%d  |  %d sensors  |  TLS=%s  |  INTERVAL_SCALE=%.2f",
        MQTT_HOST,
        MQTT_PORT,
        len(sensors),
        tls is not None,
        INTERVAL_SCALE,
    )

    while True:
        try:
            async with aiomqtt.Client(MQTT_HOST, MQTT_PORT, tls_context=tls) as client:
                log.info("Connected. Publishing… (Ctrl+C to stop)")
                await asyncio.gather(*[_safe_loop(s, client) for s in sensors])
        except aiomqtt.MqttError as exc:
            log.warning("MQTT disconnected: %s — retrying in 5s", exc)
            await asyncio.sleep(5)
        except KeyboardInterrupt:
            log.info("Stopped.")
            return


if __name__ == "__main__":
    asyncio.run(main())
