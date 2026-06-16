"""PIR motion sensor — Poisson arrivals with day/night rate."""

import asyncio
import random
from datetime import UTC, datetime

import aiomqtt
from base import BaseSensor
from config import INTERVAL_SCALE

# Events per hour by room during active hours
_DAY_RATE = {"living_room": 12, "bedroom": 3}
_NIGHT_RATE = {"living_room": 0.5, "bedroom": 1}


class MotionSensor(BaseSensor):
    """Publishes routine motion to home/{room}/presence (not /alert)."""

    def __init__(self, room: str) -> None:
        super().__init__(f"mock-pir-{room}", room)
        self._topic = f"home/{room}/presence"

    def _inter_arrival_seconds(self) -> float:
        hour = datetime.now(UTC).hour
        active = 8 <= hour <= 22
        rate_per_hour = _DAY_RATE.get(self.room, 6) if active else _NIGHT_RATE.get(self.room, 0.3)
        return random.expovariate(rate_per_hour / 3600)

    async def loop(self, client: aiomqtt.Client) -> None:
        while True:
            wait = min(self._inter_arrival_seconds(), 1800) * INTERVAL_SCALE
            await asyncio.sleep(wait)
            await self.publish(
                client,
                self._topic,
                {
                    "tier": 2,
                    "confidence": round(random.uniform(0.78, 0.99), 2),
                    "duration_s": round(random.uniform(0.5, 8.0), 1),
                },
            )
