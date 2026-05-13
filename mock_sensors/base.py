import json
import logging
from datetime import UTC, datetime

import aiomqtt

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
