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
        logger.debug("[%s] %-35s %s", self.device_id, topic, data)

    async def loop(self, client: aiomqtt.Client) -> None:
        raise NotImplementedError
