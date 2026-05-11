"""Connectivity monitor — pings VPS heartbeat and publishes status to MQTT.

Publishes to system/connectivity every 30s:
  {"vps": "up"|"down", "latency_ms": int|null, "checked_at": ISO}

Also maintains a SQLite queue for Telegram messages when VPS is down.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiomqtt
import httpx

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 30.0
CONNECTIVITY_TOPIC = "system/connectivity"
TELEGRAM_QUEUE_DB = Path("/tmp/telegram_queue.db")  # noqa: S108


class ConnectivityMonitor:
    def __init__(
        self,
        vps_heartbeat_url: str,
        mqtt_host: str,
        mqtt_port: int = 1883,
    ) -> None:
        self._vps_url = vps_heartbeat_url
        self._mqtt_host = mqtt_host
        self._mqtt_port = mqtt_port
        self._vps_status: str = "unknown"
        self._init_telegram_queue()

    def _init_telegram_queue(self) -> None:
        with sqlite3.connect(TELEGRAM_QUEUE_DB) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS queue "
                "(id INTEGER PRIMARY KEY, payload TEXT, ts TEXT)"
            )

    async def _ping_vps(self) -> tuple[str, int | None]:
        """Returns ('up'|'down', latency_ms|None)."""
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(self._vps_url)
                latency = int((time.monotonic() - t0) * 1000)
                return ("up" if resp.status_code < 500 else "down", latency)
        except Exception:
            return ("down", None)

    def queue_telegram_message(self, payload: dict[str, Any]) -> None:
        """Queue a Telegram message for delivery when VPS is back."""
        with sqlite3.connect(TELEGRAM_QUEUE_DB) as conn:
            conn.execute(
                "INSERT INTO queue (payload, ts) VALUES (?, ?)",
                (json.dumps(payload), datetime.now(UTC).isoformat()),
            )

    async def flush_telegram_queue(
        self,
        send_fn: Callable[[dict[str, Any]], Coroutine[Any, Any, None]],
    ) -> int:
        """Send all queued Telegram messages. Returns count sent."""
        with sqlite3.connect(TELEGRAM_QUEUE_DB) as conn:
            rows = conn.execute("SELECT id, payload FROM queue ORDER BY id").fetchall()
            sent = 0
            for row_id, payload_str in rows:
                try:
                    await send_fn(json.loads(payload_str))
                    conn.execute("DELETE FROM queue WHERE id = ?", (row_id,))
                    sent += 1
                except Exception:
                    logger.exception("Failed to send queued Telegram message %d", row_id)
            return sent

    async def run(self) -> None:
        while True:
            status, latency = await self._ping_vps()
            prev = self._vps_status
            self._vps_status = status

            payload = {
                "vps": status,
                "latency_ms": latency,
                "checked_at": datetime.now(UTC).isoformat(),
            }

            try:
                async with aiomqtt.Client(self._mqtt_host, self._mqtt_port) as mqtt:
                    await mqtt.publish(CONNECTIVITY_TOPIC, json.dumps(payload))
            except Exception:
                logger.warning("MQTT publish failed for connectivity status")

            if status == "up" and prev == "down":
                logger.info("VPS back online — latency %dms", latency or 0)
            elif status == "down" and prev != "down":
                logger.warning("VPS went offline")

            await asyncio.sleep(HEARTBEAT_INTERVAL)


if __name__ == "__main__":
    import os

    monitor = ConnectivityMonitor(
        vps_heartbeat_url=os.environ.get("VPS_HEARTBEAT_URL", "http://vps.example.com/health"),
        mqtt_host=os.environ.get("MQTT_HOST", "mosquitto"),
        mqtt_port=int(os.environ.get("MQTT_PORT", "1883")),
    )
    asyncio.run(monitor.run())
