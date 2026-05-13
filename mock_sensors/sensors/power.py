"""Smart plug power meter (PZEM-004T-like) with realistic load profiles."""

import asyncio
import random
from datetime import UTC, datetime

import aiomqtt
from base import BaseSensor
from config import INTERVAL_SCALE

# Watt ranges by room and hour block
# (morning, midday, evening, night)
_LOAD_PROFILE = {
    "living_room": {  # TV + lights + misc
        range(6, 9): (80, 250),
        range(9, 17): (20, 80),
        range(17, 23): (150, 380),
        range(23, 24): (10, 30),
        range(0, 6): (5, 15),
    },
    "kitchen": {  # appliances: kettle, microwave, fridge
        range(6, 9): (200, 1800),
        range(9, 12): (30, 200),
        range(12, 14): (200, 2000),
        range(14, 18): (30, 150),
        range(18, 21): (200, 2500),
        range(21, 24): (30, 100),
        range(0, 6): (30, 60),  # fridge compressor
    },
}


def _wattage(room: str) -> float:
    hour = datetime.now(UTC).hour
    profile = _LOAD_PROFILE.get(room, {})
    for hours_range, (lo, hi) in profile.items():
        if hour in hours_range:
            return round(random.uniform(lo, hi), 1)
    return round(random.uniform(20, 60), 1)


class PowerSensor(BaseSensor):
    """Publishes power_w, voltage_v, current_a to home/{room}/sensors every 10 s."""

    def __init__(self, room: str) -> None:
        super().__init__(f"mock-pzem-{room}", room)
        self._topic = f"home/{room}/sensors"

    async def loop(self, client: aiomqtt.Client) -> None:
        while True:
            watts = _wattage(self.room)
            voltage = round(random.uniform(219.5, 230.5), 1)
            current = round(watts / voltage, 3)
            await self.publish(
                client,
                self._topic,
                {
                    "tier": 0,
                    "power_w": watts,
                    "voltage_v": voltage,
                    "current_a": current,
                    "energy_kwh": round(random.uniform(0.0, 0.003), 4),
                },
            )
            await asyncio.sleep((10 + random.uniform(-1, 1)) * INTERVAL_SCALE)
