"""Identity-tier hysteresis: a borderline-confidence track must resolve to one
stable label instead of flapping Vlad↔Vlad? around COSINE_KNOWN_THRESHOLD and
flooding camera/identity. Regression for the live-stream event spam.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hub.edge.cv.pipeline import CVPipeline


def _pipeline() -> CVPipeline:
    """CVPipeline without any model load (construction is cheap + Hailo-free)."""
    return CVPipeline(
        rtsp_url="rtsp://x",
        hef_path=Path("/nonexistent.hef"),
        pose_hef_path=None,
        mqtt_host="localhost",
        mqtt_port=1883,
        room="test",
    )


def test_tier_stays_known_through_borderline_dip() -> None:
    """Once known (>=0.6), a dip to 0.57 (>=0.55 exit) keeps the track 'known'."""
    p = _pipeline()
    assert p._resolve_tier(1, "Vlad", 0.65) == "known"
    # These are exactly the sims that flapped Vlad↔Vlad? in the live trace.
    assert p._resolve_tier(1, "Vlad", 0.585) == "known"
    assert p._resolve_tier(1, "Vlad", 0.594) == "known"
    assert p._resolve_tier(1, "Vlad", 0.602) == "known"


def test_tier_downgrades_only_below_exit() -> None:
    p = _pipeline()
    assert p._resolve_tier(1, "Vlad", 0.65) == "known"
    # Below the 0.55 exit threshold → drops to uncertain.
    assert p._resolve_tier(1, "Vlad", 0.50) == "uncertain"
    # From uncertain, 0.57 is NOT enough to re-enter known (needs 0.6).
    assert p._resolve_tier(1, "Vlad", 0.57) == "uncertain"
    # Clearing the full enter threshold upgrades again.
    assert p._resolve_tier(1, "Vlad", 0.62) == "known"


def test_tier_resets_on_winner_change() -> None:
    """A new winning name gets no sticky carryover from the previous identity."""
    p = _pipeline()
    assert p._resolve_tier(1, "Vlad", 0.65) == "known"
    # Anita at 0.57 must not inherit Vlad's 'known' stickiness.
    assert p._resolve_tier(1, "Anita", 0.57) == "uncertain"


def test_resolve_identity_stable_label_under_flapping_sim() -> None:
    """End-to-end: feeding the borderline sim sequence from the live trace yields
    a single stable resolved string (no Vlad↔Vlad? toggling)."""
    p = _pipeline()
    sims = [0.65, 0.585, 0.607, 0.629, 0.594, 0.615, 0.597, 0.602]
    labels = set()
    for s in sims:
        result = SimpleNamespace(identity="Vlad", similarity=s, track_id=7)
        resolved, _ = p._resolve_identity(result)
        labels.add(resolved)
    # Without hysteresis this set would contain both "Vlad" and "Vlad?".
    assert labels == {"Vlad"}


def test_resolve_tier_cleared_state_starts_unknown() -> None:
    """A fresh track must clear the full enter thresholds (no leaked state)."""
    p = _pipeline()
    # 0.57 from a cold track is uncertain, not known.
    assert p._resolve_tier(99, "Vlad", 0.57) == "uncertain"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
