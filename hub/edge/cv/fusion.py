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
    "person": {"camera": 0.8, "pir": 0.5, "combined_bonus": 0.1},
}
FUSION_WINDOW_SEC = 30
# Confidence multiplier applied to person events when the camera sees a person
# but no PIR trigger exists for that room in the fusion window.  A PIR-less
# person detection is a possible glare / false-positive (see §7.2).
PERSON_NO_PIR_FACTOR: float = 0.7


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

    def _person_pir_factor(self, room: str) -> float:
        """Return <1.0 when camera sees a person but no PIR triggered recently.

        This penalises glare / false-positive person detections that lack a
        corresponding passive-IR event for the same room (§7.2 cross-check).
        """
        buf = self._buffers.get(room)
        if buf is None:
            return PERSON_NO_PIR_FACTOR
        has_pir = any(
            ev.get("sensor_type") == "pir" and float(ev.get("value", 0)) > 0
            for _, ev in buf.sensor_events
        )
        return 1.0 if has_pir else PERSON_NO_PIR_FACTOR

    def ingest_camera(self, room: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if room not in self._buffers:
            self._buffers[room] = RoomBuffer()
        self._buffers[room].camera_events.append((time.time(), payload))
        self._prune(room)
        return self._maybe_fuse(room, payload.get("event_type", ""))

    def ingest_detection_frame(self, room: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Handle a full-frame detection payload (``event_type="detection"``).

        Extracts per-label max-confidence from ``dets``, stores synthetic
        per-label camera events so ``_compute_confidence`` can find them, then
        returns one fused event per label that has a fusion rule.  Person events
        are penalised by ``PERSON_NO_PIR_FACTOR`` when no PIR trigger exists for
        the room in the current window (§7.2 cross-check).
        """
        if room not in self._buffers:
            self._buffers[room] = RoomBuffer()

        ts = time.time()
        dets: list[dict[str, Any]] = payload.get("dets", [])

        # Extract per-label max confidence and store as synthetic camera events
        # so _compute_confidence can look them up by event_type.
        labels_conf: dict[str, float] = {}
        for det in dets:
            label = det.get("label", "")
            if label in FUSION_WEIGHTS:
                conf = float(det.get("confidence", FUSION_WEIGHTS[label]["camera"]))
                labels_conf[label] = max(labels_conf.get(label, 0.0), conf)

        for label, conf in labels_conf.items():
            self._buffers[room].camera_events.append(
                (ts, {"event_type": label, "confidence": conf})
            )

        self._prune(room)

        results: list[dict[str, Any]] = []
        for label in labels_conf:
            fused = self._maybe_fuse(room, label)
            if fused is None:
                continue
            if label == "person":
                factor = self._person_pir_factor(room)
                if factor < 1.0:
                    fused = dict(fused)
                    fused["confidence"] = round(fused["confidence"] * factor, 4)
                    fused["pir_adjusted"] = True
            results.append(fused)

        return results

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
                if topic_str.endswith("/camera/event"):
                    for fused in self.ingest_detection_frame(room, payload):
                        await client.publish(f"home/{room}/event/fused", json.dumps(fused))
                elif topic_str.endswith("/sensors"):
                    fused = self.ingest_sensor(room, payload)
                    if fused is not None:
                        await client.publish(f"home/{room}/event/fused", json.dumps(fused))


def _sensor_key_for(event_type: str) -> str | None:
    mapping = {
        "fire": "smoke_sensor",
        "motion": "pir",
        "gas": "gas_sensor",
    }
    return mapping.get(event_type)
