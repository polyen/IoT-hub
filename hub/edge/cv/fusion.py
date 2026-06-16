from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import aiomqtt

logger = logging.getLogger(__name__)

FUSION_WEIGHTS: dict[str, dict[str, float]] = {
    "fire": {"camera": 0.7, "smoke_sensor": 0.6, "combined_bonus": 0.2},
    "motion": {"camera": 0.8, "pir": 0.5, "combined_bonus": 0.15},
    "gas": {"camera": 0.3, "gas_sensor": 0.9, "combined_bonus": 0.1},
    "person": {"camera": 0.8, "pir": 0.5, "combined_bonus": 0.1},
}
FUSION_WINDOW_SEC = 30
# Minimum seconds between two published fused events of the same type in the
# same room.  Prevents 15-FPS camera frames from flooding the events feed.
FUSED_COOLDOWN_SEC: float = 60.0
# The cooldown is bypassed when confidence rises by at least this much since the
# last emission — i.e. an escalation (a second modality corroborating the event,
# or a stronger detection) must not be silently dropped.  Without this, a fire
# confirmed by a smoke sensor seconds after the camera-only detection would be
# suppressed for a full minute.
FUSED_ESCALATION_DELTA: float = 0.05
# Confidence multiplier applied to person events when the camera sees a person
# but no PIR trigger exists for that room in the fusion window.  A PIR-less
# person detection is a possible glare / false-positive (see §7.2).
PERSON_NO_PIR_FACTOR: float = 0.7
# Alert types (home/{room}/alert) that act as a passive-presence (PIR-equivalent)
# signal. Both the mock PIR and the Zigbee bridge publish motion/presence as an
# *alert*, not a /sensors message — see ingest_alert.
_PRESENCE_ALERT_TYPES = frozenset({"motion", "presence", "occupancy"})


@dataclass
class RoomBuffer:
    camera_events: list[tuple[float, dict[str, Any]]] = field(default_factory=list)
    sensor_events: list[tuple[float, dict[str, Any]]] = field(default_factory=list)


class FusionEngine:
    def __init__(self) -> None:
        self._buffers: dict[str, RoomBuffer] = {}
        # {room: {event_type: (last_published_monotonic, last_confidence)}} —
        # suppresses re-publishing the same fused event type within
        # FUSED_COOLDOWN_SEC unless confidence escalates (see _maybe_fuse).
        self._last_fused: dict[str, dict[str, tuple[float, float]]] = {}

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

    def ingest_alert(self, room: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Map a presence/motion *alert* into a PIR-equivalent sensor event.

        Both the mock PIR (``mock_sensors/sensors/motion.py``) and the Zigbee
        bridge publish motion/presence to ``home/{room}/alert`` — *not* to
        ``/sensors`` — so without this the passive-IR signal never reaches motion
        fusion nor the person cross-check (``_person_pir_factor``). Non-presence
        alerts (door / water-leak / fall) have their own paths or none and are
        ignored here.
        """
        if payload.get("alert_type") not in _PRESENCE_ALERT_TYPES:
            return None
        return self.ingest_sensor(
            room,
            {
                "event_type": "motion",
                "sensor_type": "pir",
                "value": 1,
                "confidence": float(payload.get("confidence", FUSION_WEIGHTS["motion"]["pir"])),
            },
        )

    def _maybe_fuse(self, room: str, event_type: str) -> dict[str, Any] | None:
        if not event_type:
            return None
        confidence = self._compute_confidence(room, event_type)
        if confidence == 0.0:
            return None
        # Rate-limit: skip if we already emitted the same event type for this
        # room within the cooldown window (avoids per-frame flooding) — unless
        # confidence has escalated since the last emission (a corroborating
        # second source / stronger detection must still get through).
        now = time.monotonic()
        room_last = self._last_fused.setdefault(room, {})
        last_ts, last_conf = room_last.get(event_type, (0.0, 0.0))
        within_cooldown = now - last_ts < FUSED_COOLDOWN_SEC
        escalated = confidence >= last_conf + FUSED_ESCALATION_DELTA
        if within_cooldown and not escalated:
            return None
        room_last[event_type] = (now, confidence)

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
        """Run the fusion engine, retrying MQTT indefinitely on disconnect.

        Never raises MqttError — mirrors the retry pattern in CVPipeline.run()
        so that asyncio.gather() never cancels the pipeline task due to a
        transient MQTT blip.
        """
        import asyncio  # noqa: PLC0415

        _retry_delay = 5
        while True:
            try:
                async with aiomqtt.Client(mqtt_host, mqtt_port) as client:
                    await client.subscribe("home/+/camera/event")
                    await client.subscribe("home/+/sensors")
                    await client.subscribe("home/+/alert")
                    _retry_delay = 5  # reset on successful connect
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
                            fused_sensor = self.ingest_sensor(room, payload)
                            if fused_sensor is not None:
                                await client.publish(
                                    f"home/{room}/event/fused", json.dumps(fused_sensor)
                                )
                        elif topic_str.endswith("/alert"):
                            fused_alert = self.ingest_alert(room, payload)
                            if fused_alert is not None:
                                await client.publish(
                                    f"home/{room}/event/fused", json.dumps(fused_alert)
                                )
            except aiomqtt.MqttError as exc:
                logger.warning("Fusion MQTT error (%s) — retrying in %ds", exc, _retry_delay)
                await asyncio.sleep(_retry_delay)
                _retry_delay = min(_retry_delay * 2, 60)


def _sensor_key_for(event_type: str) -> str | None:
    mapping = {
        "fire": "smoke_sensor",
        "motion": "pir",
        "gas": "gas_sensor",
    }
    return mapping.get(event_type)
