"""Magnetic door/window contact sensor."""

import asyncio
import random
from datetime import UTC, datetime

import aiomqtt
from base import BaseSensor
from config import INTERVAL_SCALE

# Average door openings per hour by room during active hours
_OPEN_RATE = {"bedroom": 4, "kitchen": 10, "living_room": 6}


class DoorSensor(BaseSensor):
    """Publishes door open/close pairs to home/{room}/alert."""

    def __init__(self, room: str, door_name: str = "main") -> None:
        super().__init__(f"mock-door-{room}-{door_name}", room)
        self._topic = f"home/{room}/alert"
        self._door_name = door_name

    def _inter_event_seconds(self) -> float:
        hour = datetime.now(UTC).hour
        active = 7 <= hour <= 23
        rate = _OPEN_RATE.get(self.room, 5) if active else 0.3
        return random.expovariate(rate / 3600)

    async def loop(self, client: aiomqtt.Client) -> None:
        while True:
            wait = min(self._inter_event_seconds(), 3600) * INTERVAL_SCALE
            await asyncio.sleep(wait)

            # Open event
            await self.publish(
                client,
                self._topic,
                {
                    "tier": 2,
                    "alert_type": "door_open",
                    "door": self._door_name,
                    "confidence": 1.0,
                },
            )

            # Door stays open 2–120 seconds
            open_duration = random.uniform(2, 120) * INTERVAL_SCALE
            await asyncio.sleep(open_duration)

            # Close event
            await self.publish(
                client,
                self._topic,
                {
                    "tier": 2,
                    "alert_type": "door_close",
                    "door": self._door_name,
                    "open_duration_s": round(open_duration / INTERVAL_SCALE, 1),
                    "confidence": 1.0,
                },
            )
