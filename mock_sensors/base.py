import asyncio
import json
import logging
from datetime import UTC, datetime

import aiomqtt
from config import INTERVAL_SCALE

logger = logging.getLogger(__name__)


class BaseSensor:
    """Shared MQTT publish logic for all mock sensors."""

    def __init__(self, device_id: str, room: str) -> None:
        self.device_id = device_id
        self.room = room

    async def publish(self, client: aiomqtt.Client, topic: str, payload: dict) -> None:
        data = {"device_id": self.device_id, "ts": datetime.now(UTC).isoformat(), **payload}
        await client.publish(topic, json.dumps(data), qos=1)

        # compact one-liner: topic + key numeric fields only
        summary = {
            k: v for k, v in payload.items() if isinstance(v, int | float) and k not in ("tier",)
        }
        logger.info("→ %-38s %s", topic, summary)


class Actuator(BaseSensor):
    """Base for *controllable* mock devices (light, thermostat, …).

    Closes the loop the read-only sensors leave open: the UI sliders / scene
    runs publish a command via ``POST /api/devices/{device_id}/command`` →
    Redis ``mqtt:publish:{topic}`` → broker.  An actuator subscribes to that
    command topic, applies the payload, and publishes its resulting state on
    ``home/{room}/{kind}/state`` — which ``mqtt_subscriber._handle_device_state``
    records under ``home:state:{device_id}`` (via the payload ``device_id``
    fallback, so no controllable-registry entry is required for the echo).

    Subclasses implement :meth:`apply` (mutate internal state from a command
    payload) and :meth:`state_payload` (current state as a flat dict).
    """

    kind: str = "device"
    #: seconds between unsolicited state heartbeats (republish so a late
    #: subscriber / cache miss still converges)
    state_interval: float = 30.0

    def __init__(self, device_id: str, room: str) -> None:
        super().__init__(device_id, room)
        self.state_topic = f"home/{room}/{self.kind}/state"

    @property
    def command_topics(self) -> list[str]:
        """Topics this actuator listens on.

        - ``home/{device_id}/cmd`` — the REST UI default (``routes/devices.py``).
        - ``home/{room}/{kind}/cmd`` — the voice/``DeviceRegistry`` default.

        Subscribing to both keeps the slider, scene-run, and voice paths working
        regardless of which default the placement's ``config.mqtt_topic`` uses.
        """
        return [f"home/{self.device_id}/cmd", f"home/{self.room}/{self.kind}/cmd"]

    def apply(self, payload: dict) -> None:  # pragma: no cover - overridden
        """Mutate internal state from a command payload."""
        raise NotImplementedError

    def state_payload(self) -> dict:  # pragma: no cover - overridden
        """Return current state as a flat dict (published on the state topic)."""
        raise NotImplementedError

    async def publish_state(self, client: aiomqtt.Client) -> None:
        await self.publish(client, self.state_topic, self.state_payload())

    async def handle_command(self, client: aiomqtt.Client, payload: dict) -> None:
        self.apply(payload)
        logger.info("← %-30s cmd %s → %s", self.device_id, payload, self.state_payload())
        await self.publish_state(client)

    async def loop(self, client: aiomqtt.Client) -> None:
        """Publish initial state, then heartbeat it periodically."""
        while True:
            await self.publish_state(client)
            await asyncio.sleep(self.state_interval * INTERVAL_SCALE)
