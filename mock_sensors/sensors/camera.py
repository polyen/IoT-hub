"""Simulates CV pipeline events: person detection, fire/smoke, fall."""

import asyncio
import random
from datetime import UTC, datetime

import aiomqtt
from base import BaseSensor
from config import INTERVAL_SCALE

# Person detection rate (events/hour) by room during day
_PERSON_RATE_DAY = {"living_room": 20, "kitchen": 8, "bedroom": 4}
_PERSON_RATE_NIGHT = {"living_room": 1, "kitchen": 0.5, "bedroom": 0.5}

# Rare anomaly probabilities (per person detection event)
_FIRE_PROB = 0.003  # ~1 in 300
_FALL_PROB = 0.008  # ~1 in 125


class CameraEventSensor(BaseSensor):
    """Publishes to home/{room}/camera/event using YOLO26n detections."""

    def __init__(self, room: str) -> None:
        super().__init__(f"mock-cam-{room}", room)
        self._topic = f"home/{room}/camera/event"

    def _random_bbox(self) -> list[float]:
        x1 = round(random.uniform(0.05, 0.6), 3)
        y1 = round(random.uniform(0.05, 0.6), 3)
        x2 = round(x1 + random.uniform(0.1, 0.35), 3)
        y2 = round(y1 + random.uniform(0.2, 0.5), 3)
        return [min(x1, 0.95), min(y1, 0.95), min(x2, 1.0), min(y2, 1.0)]

    def _inter_arrival_seconds(self) -> float:
        hour = datetime.now(UTC).hour
        active = 8 <= hour <= 22
        rate = (
            _PERSON_RATE_DAY.get(self.room, 10)
            if active
            else _PERSON_RATE_NIGHT.get(self.room, 0.5)
        )
        return random.expovariate(rate / 3600)

    async def loop(self, client: aiomqtt.Client) -> None:
        while True:
            wait = min(self._inter_arrival_seconds(), 1800) * INTERVAL_SCALE
            await asyncio.sleep(wait)

            # Decide event type
            r = random.random()
            if r < _FIRE_PROB:
                event_type = "fire_detected"
                confidence = round(random.uniform(0.70, 0.96), 3)
                tier = 2
            elif r < _FIRE_PROB + _FALL_PROB:
                event_type = "fall_detected"
                confidence = round(random.uniform(0.72, 0.94), 3)
                tier = 2
            else:
                event_type = "person_detected"
                confidence = round(random.uniform(0.80, 0.99), 3)
                tier = 2

            await self.publish(
                client,
                self._topic,
                {
                    "tier": tier,
                    "event_type": event_type,
                    "confidence": confidence,
                    "bbox": self._random_bbox(),
                    "model_version": "yolo26n-v1.0",
                    "latency_ms": random.randint(18, 65),
                },
            )
