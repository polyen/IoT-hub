"""DHT22-like temperature + humidity sensor (one per room)."""

import asyncio
import math
import random
from datetime import UTC, datetime

import aiomqtt
from base import BaseSensor
from config import INTERVAL_SCALE

# Realistic base temperatures per room
_BASE_TEMP = {"living_room": 21.5, "kitchen": 23.0, "bedroom": 20.0}
_BASE_HUM = {"living_room": 47.0, "kitchen": 55.0, "bedroom": 44.0}


class TempHumiditySensor(BaseSensor):
    """Publishes temperature + humidity to home/{room}/sensors every 30 s."""

    def __init__(self, room: str) -> None:
        super().__init__(f"mock-dht22-{room}", room)
        self._base_temp = _BASE_TEMP.get(room, 21.0)
        self._base_hum = _BASE_HUM.get(room, 48.0)
        self._topic = f"home/{room}/sensors"

    def _temperature(self) -> float:
        hour = datetime.now(UTC).hour
        # Diurnal cycle: coldest ~5 AM, warmest ~2 PM
        diurnal = 1.8 * math.sin(2 * math.pi * (hour - 5) / 24)
        return round(self._base_temp + diurnal + random.gauss(0, 0.2), 1)

    def _humidity(self) -> float:
        return round(max(20.0, min(95.0, self._base_hum + random.gauss(0, 1.5))), 1)

    async def loop(self, client: aiomqtt.Client) -> None:
        while True:
            await self.publish(
                client,
                self._topic,
                {
                    "tier": 1,
                    "temperature": self._temperature(),
                    "humidity": self._humidity(),
                },
            )
            await asyncio.sleep((30 + random.uniform(-3, 3)) * INTERVAL_SCALE)
