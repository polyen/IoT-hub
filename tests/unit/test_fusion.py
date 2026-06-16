from __future__ import annotations

import time

from hub.edge.cv.fusion import FUSION_WINDOW_SEC, FusionEngine


def make_engine() -> FusionEngine:
    return FusionEngine()


# ── 1: Camera fire + MQ-2 spike → confidence > 0.9 ───────────────────────────


def test_fire_camera_and_sensor_high_confidence() -> None:
    eng = make_engine()
    eng.ingest_camera("kitchen", {"event_type": "fire", "confidence": 0.9})
    fused = eng.ingest_sensor(
        "kitchen", {"event_type": "fire", "sensor_type": "smoke_sensor", "confidence": 0.85}
    )
    assert fused is not None
    assert fused["confidence"] > 0.9
    assert "camera" in fused["sources"]
    assert "smoke_sensor" in fused["sources"]


# ── 2: Camera fire only → confidence reduced (< 0.7) ─────────────────────────


def test_fire_camera_only_reduced_confidence() -> None:
    eng = make_engine()
    fused = eng.ingest_camera("kitchen", {"event_type": "fire", "confidence": 0.9})
    assert fused is not None
    assert fused["confidence"] < 0.7


# ── 3: MQ-2 spike only (no camera) → confidence < 0.7 ────────────────────────


def test_fire_sensor_only_reduced_confidence() -> None:
    eng = make_engine()
    fused = eng.ingest_sensor(
        "kitchen", {"event_type": "fire", "sensor_type": "smoke_sensor", "confidence": 0.85}
    )
    assert fused is not None
    assert fused["confidence"] < 0.7


# ── 4: Events older than window are pruned ────────────────────────────────────


def test_events_pruned_after_window() -> None:
    eng = make_engine()
    eng.ingest_camera("kitchen", {"event_type": "fire", "confidence": 0.9})
    buf = eng._buffers["kitchen"]
    old_ts = time.time() - FUSION_WINDOW_SEC - 1
    buf.camera_events[0] = (old_ts, buf.camera_events[0][1])
    eng._prune("kitchen")
    assert len(buf.camera_events) == 0


# ── 5: Unknown event_type → confidence 0.0 ───────────────────────────────────


def test_unknown_event_type_zero_confidence() -> None:
    eng = make_engine()
    fused = eng.ingest_camera("kitchen", {"event_type": "alien_invasion", "confidence": 0.99})
    assert fused is None or fused["confidence"] == 0.0


# ── 6: A presence/motion event (PIR or Zigbee) feeds motion fusion ───────────


def test_presence_event_feeds_motion_fusion() -> None:
    # mock PIR and the Zigbee bridge both publish motion to home/{room}/presence.
    eng = make_engine()
    fused = eng.ingest_presence("bedroom", {"confidence": 0.9})
    assert fused is not None
    assert fused["event_type"] == "motion"
    assert "pir" in fused["sources"]


def test_presence_event_without_confidence_uses_default() -> None:
    eng = make_engine()
    assert eng.ingest_presence("bedroom", {}) is not None


# ── 7: Presence satisfies the person PIR cross-check (§7.2) ───────────────────


def test_person_penalised_without_presence() -> None:
    eng = make_engine()
    results = eng.ingest_detection_frame(
        "bedroom", {"dets": [{"label": "person", "confidence": 0.9}]}
    )
    person = next(r for r in results if r["event_type"] == "person")
    assert person.get("pir_adjusted") is True


def test_person_not_penalised_with_presence() -> None:
    eng = make_engine()
    # Presence arrives first (corroborating a real occupant)…
    eng.ingest_presence("bedroom", {"confidence": 1.0})
    # …then the camera sees a person → no glare penalty.
    results = eng.ingest_detection_frame(
        "bedroom", {"dets": [{"label": "person", "confidence": 0.9}]}
    )
    person = next(r for r in results if r["event_type"] == "person")
    assert "pir_adjusted" not in person
