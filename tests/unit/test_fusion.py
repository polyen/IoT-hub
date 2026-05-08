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
