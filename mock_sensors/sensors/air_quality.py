"""SCD41 CO₂ + SGP30 TVOC + PMS5003 PM2.5 air quality sensor."""

import asyncio
import random
from datetime import UTC, datetime

import aiomqtt
from base import BaseSensor
from config import INTERVAL_SCALE

# Kitchen CO2 spikes during cooking hours
_COOKING_HOURS = {7, 8, 12, 13, 18, 19, 20}


class AirQualitySensor(BaseSensor):
    """Publishes CO₂, TVOC, PM2.5 to home/{room}/sensors every 60 s."""

    def __init__(self, room: str) -> None:
        super().__init__(f"mock-aq-{room}", room)
        self._topic = f"home/{room}/sensors"

    def _co2(self) -> int:
        base = 420  # outdoor baseline ppm
        # Kitchen: high spikes during cooking
        if self.room == "kitchen" and datetime.now(UTC).hour in _COOKING_HOURS:
            base = random.randint(800, 1800)
        else:
            base = random.randint(450, 750)
        return base + random.randint(-20, 20)

    def _tvoc(self) -> float:
        # μg/m³ — higher in kitchen during cooking
        if self.room == "kitchen" and datetime.now(UTC).hour in _COOKING_HOURS:
            return round(random.uniform(0.3, 1.2), 3)
        return round(random.uniform(0.05, 0.25), 3)

    def _pm25(self) -> float:
        # μg/m³ — WHO safe limit 15 μg/m³
        if self.room == "kitchen" and datetime.now(UTC).hour in _COOKING_HOURS:
            return round(random.uniform(15, 55), 1)
        return round(random.uniform(3, 14), 1)

    async def loop(self, client: aiomqtt.Client) -> None:
        while True:
            await self.publish(
                client,
                self._topic,
                {
                    "tier": 1,
                    "co2_ppm": self._co2(),
                    "tvoc_ug_m3": self._tvoc(),
                    "pm25_ug_m3": self._pm25(),
                },
            )
            await asyncio.sleep((60 + random.uniform(-5, 5)) * INTERVAL_SCALE)
