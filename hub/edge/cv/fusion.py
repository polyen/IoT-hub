from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import aiomqtt

FUSION_WEIGHTS: dict[str, dict[str, float]] = {
    "fire": {"camera": 0.7, "smoke_sensor": 0.6, "combined_bonus": 0.2},
    "motion": {"camera": 0.8, "pir": 0.5, "combined_bonus": 0.15},
    "gas": {"camera": 0.3, "gas_sensor": 0.9, "combined_bonus": 0.1},
}
FUSION_WINDOW_SEC = 30


@dataclass
class RoomBuffer:
    camera_events: list[tuple[float, dict[str, Any]]] = field(default_factory=list)
    sensor_events: list[tuple[float, dict[str, Any]]] = field(default_factory=list)


class FusionEngine:
    def __init__(self) -> None:
        self._buffers: dict[str, RoomBuffer] = {}

    def _prune(self, room: str) -> None:
        buf = self._buffers.get(room)
        if buf is None:
            return
        cutoff = time.time() - FUSION_WINDOW_SEC
        buf.camera_events = [(ts, ev) for ts, ev in buf.camera_events if ts >= cutoff]
        buf.sensor_events = [(ts, ev) for ts, ev in buf.sensor_events if ts >= cutoff]

    def _compute_confidence(self, room: str, event_type: str) -> float:
        weights = FUSION_WEIGHTS.get(event_type)
        if weights is None:
            return 0.0
        buf = self._buffers.get(room)
        if buf is None:
            return 0.0

        camera_conf = 0.0
        for _ts, ev in buf.camera_events:
            if ev.get("event_type") == event_type:
                camera_conf = max(camera_conf, float(ev.get("confidence", weights["camera"])))

        sensor_key = _sensor_key_for(event_type)
        sensor_conf = 0.0
        for _ts, ev in buf.sensor_events:
            if ev.get("event_type") == event_type or (
                sensor_key and ev.get("sensor_type") == sensor_key
            ):
                sensor_conf = max(
                    sensor_conf,
                    float(ev.get("confidence", weights.get(sensor_key or "", 0.0))),
                )

        has_camera = camera_conf > 0.0
        has_sensor = sensor_conf > 0.0

        if has_camera and has_sensor:
            combined = (
                camera_conf * weights["camera"]
                + sensor_conf * weights.get(sensor_key or "", 0.5)
                + weights["combined_bonus"]
            )
            return min(combined, 1.0)
        if has_camera:
            return camera_conf * weights["camera"]
        if has_sensor:
            return sensor_conf * weights.get(sensor_key or "", 0.5)
        return 0.0

    def ingest_camera(self, room: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if room not in self._buffers:
            self._buffers[room] = RoomBuffer()
        self._buffers[room].camera_events.append((time.time(), payload))
        self._prune(room)
        return self._maybe_fuse(room, payload.get("event_type", ""))

    def ingest_sensor(self, room: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if room not in self._buffers:
            self._buffers[room] = RoomBuffer()
        self._buffers[room].sensor_events.append((time.time(), payload))
        self._prune(room)
        return self._maybe_fuse(room, payload.get("event_type", ""))

    def _maybe_fuse(self, room: str, event_type: str) -> dict[str, Any] | None:
        if not event_type:
            return None
        confidence = self._compute_confidence(room, event_type)
        if confidence == 0.0:
            return None
        buf = self._buffers[room]
        sources: list[str] = []
        for _ts, ev in buf.camera_events:
            if ev.get("event_type") == event_type:
                sources.append("camera")
                break
        sensor_key = _sensor_key_for(event_type)
        for _ts, ev in buf.sensor_events:
            if ev.get("event_type") == event_type or (
                sensor_key and ev.get("sensor_type") == sensor_key
            ):
                if sensor_key:
                    sources.append(sensor_key)
                break
        return {
            "room": room,
            "event_type": event_type,
            "confidence": round(confidence, 4),
            "sources": sources,
            "tier": 1,
        }

    async def run(self, mqtt_host: str, mqtt_port: int) -> None:
        async with aiomqtt.Client(mqtt_host, mqtt_port) as client:
            await client.subscribe("home/+/camera/event")
            await client.subscribe("home/+/sensors")
            async for message in client.messages:
                topic_str = str(message.topic)
                parts = topic_str.split("/")
                if len(parts) < 3:
                    continue
                room = parts[1]
                try:
                    payload: dict[str, Any] = json.loads(message.payload)
                except Exception:
                    continue
                fused: dict[str, Any] | None = None
                if topic_str.endswith("/camera/event"):
                    fused = self.ingest_camera(room, payload)
                elif topic_str.endswith("/sensors"):
                    fused = self.ingest_sensor(room, payload)
                if fused is not None:
                    fused_topic = f"home/{room}/event/fused"
                    await client.publish(fused_topic, json.dumps(fused))


def _sensor_key_for(event_type: str) -> str | None:
    mapping = {
        "fire": "smoke_sensor",
        "motion": "pir",
        "gas": "gas_sensor",
    }
    return mapping.get(event_type)
