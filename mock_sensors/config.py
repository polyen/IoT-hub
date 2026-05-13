import os
from pathlib import Path

MQTT_HOST = os.getenv("MQTT_HOST", "192.168.0.53")
MQTT_PORT = int(os.getenv("MQTT_PORT", "8883"))

# Set <1 to speed up all intervals (e.g. 0.1 = 10x faster, useful for testing)
INTERVAL_SCALE = float(os.getenv("INTERVAL_SCALE", "1.0"))

ROOMS = ["living_room", "kitchen", "bedroom"]

_CERTS = Path(__file__).parent / "certs"
MQTT_TLS_CA = os.getenv("MQTT_TLS_CA", str(_CERTS / "ca.crt"))
MQTT_TLS_CERT = os.getenv("MQTT_TLS_CERT", str(_CERTS / "mock-sensors.crt"))
MQTT_TLS_KEY = os.getenv("MQTT_TLS_KEY", str(_CERTS / "mock-sensors.key"))
