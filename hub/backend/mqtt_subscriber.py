"""Async MQTT → PostgreSQL subscriber running inside FastAPI lifespan."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any

import aiomqtt
from prometheus_client import Counter

from hub.backend.config import settings
from hub.backend.db import AsyncSessionLocal
from hub.backend.models import Event

logger = logging.getLogger(__name__)

MQTT_MSGS = Counter(
    "iot_hub_mqtt_msgs_total",
    "MQTT messages received",
    ["topic", "status"],
)

SUBSCRIPTIONS = [
    "home/+/sensors",
    "home/+/alert",
    "home/+/event/fused",
    "home/+/camera/event",
    "home/+/camera/identity",
    "home/+/+/state",  # device state feedback (controllable devices)
]

# How long a face recognition result is cached in Redis for overlay enrichment.
_IDENTITY_TTL_SEC = 10

_DEAD_LETTER_KEY = "mqtt:dead-letter"
_DEAD_LETTER_MAX = 1000

_RedisClient = Any

# Per-room {track_id: last-seen monotonic ts}. A camera detection is persisted
# to the DB only when its track_id is absent here — i.e. the object just
# entered frame — so the events feed shows one row per object, not per frame.
_SEEN_TRACK_TTL_SEC = 60.0
_seen_tracks: dict[str, dict[Any, float]] = {}

# Deduplication for non-camera events (fused, sensors).  Key: "{room}/{type_}",
# value: last-persisted monotonic ts.  Prevents flooding when fusion or sensor
# nodes publish the same event type repeatedly.
_EVENT_DEDUP_TTL_SEC = 60.0
_seen_events: dict[str, float] = {}


async def run(redis_client: _RedisClient) -> None:
    while True:
        try:
            async with aiomqtt.Client(settings.mqtt_host, settings.mqtt_port) as client:
                for topic in SUBSCRIPTIONS:
                    await client.subscribe(topic)
                async for message in client.messages:
                    await _handle(message, redis_client)
        except aiomqtt.MqttError as exc:
            logger.warning("MQTT connection lost: %s — reconnecting in 5s", exc)
            await asyncio.sleep(5)
        except Exception as exc:
            logger.error("MQTT subscriber crashed: %s — restarting in 5s", exc, exc_info=True)
            await asyncio.sleep(5)


_OUTBOUND_PREFIX = "mqtt:publish:"
_OUTBOUND_PREFIX_LEN = len(_OUTBOUND_PREFIX)


async def run_outbound(redis_client: _RedisClient) -> None:
    """Bridge mqtt:publish:* Redis pub/sub → MQTT broker (backend-originated commands)."""
    while True:
        try:
            async with aiomqtt.Client(settings.mqtt_host, settings.mqtt_port) as mqtt:
                pubsub = redis_client.pubsub()
                await pubsub.psubscribe(_OUTBOUND_PREFIX + "*")
                try:
                    async for msg in pubsub.listen():
                        if msg["type"] != "pmessage":
                            continue
                        channel: str = msg["channel"]
                        topic = channel[_OUTBOUND_PREFIX_LEN:]
                        if not topic:
                            continue
                        data = msg["data"]
                        payload = data if isinstance(data, bytes | bytearray) else data.encode()
                        await mqtt.publish(topic, payload)
                        logger.debug("MQTT outbound: %s", topic)
                finally:
                    await pubsub.punsubscribe(_OUTBOUND_PREFIX + "*")
                    await pubsub.aclose()
        except aiomqtt.MqttError as exc:
            logger.warning("MQTT outbound lost: %s — reconnecting in 5s", exc)
            await asyncio.sleep(5)
        except Exception as exc:
            logger.error("MQTT outbound crashed: %s — restarting in 5s", exc, exc_info=True)
            await asyncio.sleep(5)


async def _handle(
    message: aiomqtt.Message,
    redis_client: _RedisClient,
) -> None:
    topic_str = str(message.topic)
    parts = topic_str.split("/")
    room = parts[1] if len(parts) >= 3 else None
    type_ = "/".join(parts[2:]) if len(parts) >= 3 else topic_str

    raw = message.payload if isinstance(message.payload, str | bytes) else b""

    try:
        payload: dict[str, Any] = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        await _dead_letter(redis_client, topic_str, raw, str(exc))
        MQTT_MSGS.labels(topic=type_, status="dead_letter").inc()
        return

    # Device state feedback: handled before tier check because ESPHome/Zigbee
    # devices don't include a tier field in state messages.
    if type_.endswith("/state") or type_ == "state":
        await _handle_device_state(redis_client, topic_str, payload)
        MQTT_MSGS.labels(topic=type_, status="ok").inc()
        return

    tier_raw = payload.get("tier", 1)
    try:
        tier = int(tier_raw)
    except (TypeError, ValueError):
        tier = -1

    if tier not in (0, 1, 2, 3):
        await _dead_letter(redis_client, topic_str, raw, f"invalid tier: {tier_raw!r}")
        MQTT_MSGS.labels(topic=type_, status="dead_letter").inc()
        return

    # Camera detections take a separate path: the whole frame is bridged to
    # the live-overlay channel, but only newly-seen tracks hit the DB.
    if type_ == "camera/event":
        await _handle_camera_event(redis_client, room, tier, payload)
        MQTT_MSGS.labels(topic=type_, status="ok").inc()
        return

    # Face recognition results are cached in Redis (for overlay enrichment) and
    # persisted to DB only for non-"unknown" identities to avoid log flooding.
    if type_ == "camera/identity":
        await _handle_identity_event(redis_client, room, tier, payload)
        MQTT_MSGS.labels(topic=type_, status="ok").inc()
        return

    # Deduplicate repetitive non-alert event types (sensors, fused) so the
    # events feed doesn't flood.  Alerts always bypass this check.
    if type_ != "alert" and _is_event_suppressed(room, type_):
        MQTT_MSGS.labels(topic=type_, status="suppressed").inc()
        return

    if await _persist_event(redis_client, room, type_, tier, payload) is None:
        MQTT_MSGS.labels(topic=type_, status="db_error").inc()
        return

    MQTT_MSGS.labels(topic=type_, status="ok").inc()


async def _handle_device_state(
    redis_client: _RedisClient,
    topic_str: str,
    payload: dict[str, Any],
) -> None:
    """Write device state to Redis ``home:state:{device_id}`` hash.

    The topic → device_id map is maintained by ``DeviceRegistry.load()``
    in ``home:device-state-topics``.
    """
    device_id_raw = await redis_client.hget("home:device-state-topics", topic_str)
    if device_id_raw is None:
        logger.debug("Received state on unregistered topic %s — skipping", topic_str)
        return
    device_id = device_id_raw.decode() if isinstance(device_id_raw, bytes) else str(device_id_raw)
    state_fields = {str(k): str(v) for k, v in payload.items()}
    await redis_client.hset(f"home:state:{device_id}", mapping=state_fields)
    logger.debug("Device state updated: %s → %s", device_id, state_fields)


async def _persist_event(
    redis_client: _RedisClient,
    room: str | None,
    type_: str,
    tier: int,
    payload: dict[str, Any],
) -> Event | None:
    """Write one Event row and notify WebSocket subscribers.

    Returns the persisted Event, or None if the DB write failed.
    """
    event = Event(
        timestamp=datetime.now(UTC),
        room=room,
        type=type_,
        tier=tier,
        payload=payload,
        model_version=payload.get("model_version"),
    )
    try:
        async with AsyncSessionLocal() as session:
            session.add(event)
            await session.commit()
            await session.refresh(event)
    except Exception as exc:
        logger.error("DB write failed for type %s: %s", type_, exc, exc_info=True)
        return None

    logger.info("Saved event id=%s type=%s room=%s", event.id, type_, room)
    await redis_client.publish(
        "events:new",
        json.dumps(
            {
                "id": str(event.id),
                "timestamp": event.timestamp.isoformat(),
                "room": event.room,
                "type": type_,
                "tier": tier,
                "payload": payload,
                "model_version": None,
            }
        ),
    )
    return event


def _is_event_suppressed(room: str | None, type_: str) -> bool:
    """Return True and skip DB write if an identical (room, type_) event was
    persisted within _EVENT_DEDUP_TTL_SEC.  Updates the seen-timestamp on first
    occurrence so subsequent calls within the window are suppressed."""
    key = f"{room}/{type_}"
    now = time.monotonic()
    if now - _seen_events.get(key, 0.0) < _EVENT_DEDUP_TTL_SEC:
        return True
    _seen_events[key] = now
    return False


def _new_tracks(room: str, dets: list[Any]) -> list[dict[str, Any]]:
    """Return detections whose track_id was not seen within the TTL window."""
    now = time.monotonic()
    seen = _seen_tracks.setdefault(room, {})
    for tid in [t for t, ts in seen.items() if now - ts > _SEEN_TRACK_TTL_SEC]:
        del seen[tid]
    new: list[dict[str, Any]] = []
    for det in dets:
        if not isinstance(det, dict):
            continue
        tid = det.get("track_id")
        if tid is None:
            continue
        if tid not in seen:
            new.append(det)
        seen[tid] = now
    return new


async def _handle_identity_event(
    redis_client: _RedisClient,
    room: str | None,
    tier: int,
    payload: dict[str, Any],
) -> None:
    """Cache face recognition result in Redis and persist known/uncertain identities to DB."""
    if not room:
        return
    track_id = payload.get("track_id")
    identity = payload.get("identity", "unknown")

    if track_id is not None:
        key = f"cv:identity:{room}:{track_id}"
        result = redis_client.setex(key, _IDENTITY_TTL_SEC, str(identity))
        if isinstance(result, Awaitable):
            await result

    # Don't persist pure-unknown hits — they're too noisy and carry no information.
    if identity != "unknown":
        await _persist_event(redis_client, room, "camera/identity", tier, payload)


async def _handle_camera_event(
    redis_client: _RedisClient,
    room: str | None,
    tier: int,
    payload: dict[str, Any],
) -> None:
    """Bridge a per-frame camera/event to the live overlay and persist new tracks.

    The pipeline publishes one camera/event per frame with a ``dets`` array.
    The whole frame is forwarded to ``cv:detections:{room}`` every time so the
    UI overlay stays current; a DB event is written only for track_ids not
    seen recently (see _new_tracks), so the feed shows one row per object.

    Each person detection is enriched with the latest cached face identity from
    Redis (key ``cv:identity:{room}:{track_id}``), so the frontend overlay can
    show names without waiting for the next identity inference cycle.
    """
    dets = payload.get("dets")
    if not room or not isinstance(dets, list):
        return

    # 1. Build enriched detection list, injecting cached face identities.
    enriched: list[dict[str, Any]] = []
    for d in dets:
        if not isinstance(d, dict):
            continue
        det: dict[str, Any] = {
            "bbox": d.get("bbox", []),
            "cls": d.get("label", "unknown"),
            "conf": d.get("confidence", 0.0),
            "track_id": d.get("track_id"),
            "face_id": d.get("face_id"),
            "kps": d.get("kps"),
        }
        tid = d.get("track_id")
        if tid is not None:
            cached = redis_client.get(f"cv:identity:{room}:{tid}")
            if isinstance(cached, Awaitable):
                cached = await cached
            if cached is not None:
                det["face_id"] = cached.decode() if isinstance(cached, bytes) else str(cached)
        enriched.append(det)

    # 2. Live overlay — forward the whole frame every time.
    cv_frame = json.dumps({"ts": datetime.now(UTC).isoformat(), "room": room, "dets": enriched})
    await redis_client.publish(f"cv:detections:{room}", cv_frame)

    # 2. Persist one DB event per newly-appeared track.
    for det in _new_tracks(room, dets):
        det_payload: dict[str, Any] = {
            "room": room,
            "event_type": "detection",
            "label": det.get("label", "unknown"),
            "confidence": det.get("confidence", 0.0),
            "bbox": det.get("bbox", []),
            "track_id": det.get("track_id"),
            "tier": tier,
        }
        # Carry frame_blob_ref through to the DB row so mining can locate the T0 frame.
        if det.get("frame_blob_ref"):
            det_payload["frame_blob_ref"] = det["frame_blob_ref"]
        await _persist_event(redis_client, room, "camera/event", tier, det_payload)


async def _dead_letter(
    redis_client: _RedisClient,
    topic: str,
    payload_raw: str | bytes,
    error: str,
) -> None:
    try:
        raw_str = (
            payload_raw.decode("utf-8", errors="replace")
            if isinstance(payload_raw, bytes)
            else payload_raw
        )
        entry = json.dumps({"topic": topic, "payload": raw_str, "error": error})
        lpush_result = redis_client.lpush(_DEAD_LETTER_KEY, entry)
        if isinstance(lpush_result, Awaitable):
            await lpush_result
        ltrim_result = redis_client.ltrim(_DEAD_LETTER_KEY, 0, _DEAD_LETTER_MAX - 1)
        if isinstance(ltrim_result, Awaitable):
            await ltrim_result
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to push to dead-letter list: %s", exc)
