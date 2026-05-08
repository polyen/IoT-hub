"""Mock ESP32 sensor node for integration tests — publishes real MQTT messages."""

from __future__ import annotations

import asyncio
import json
import random
import sys

import aiomqtt


async def publish_loop(host: str, port: int, room: str, count: int = 10) -> None:
    async with aiomqtt.Client(host, port) as client:
        for _ in range(count):
            payload = {
                "room": room,
                "temperature": round(random.uniform(18, 28), 1),
                "humidity": round(random.uniform(40, 70), 1),
                "gas_ppm": random.randint(100, 400),
                "pir": random.choice([True, False]),
                "tier": 1,
            }
            await client.publish(f"home/{room}/sensors", json.dumps(payload))
            await asyncio.sleep(1)


if __name__ == "__main__":
    room = sys.argv[1] if len(sys.argv) > 1 else "test_room"
    asyncio.run(publish_loop("localhost", 1883, room))
