"""Identity-tier hysteresis: a borderline-confidence track must resolve to one
stable label instead of flapping Vlad↔Vlad? around COSINE_KNOWN_THRESHOLD and
flooding camera/identity. Regression for the live-stream event spam.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hub.edge.cv.pipeline import (
    COSINE_KNOWN_THRESHOLD,
    IDENTITY_KNOWN_EXIT,
    CVPipeline,
)

# Sims expressed relative to the (env-tunable) thresholds so the test tracks any
# threshold change instead of hard-coding the old 0.6/0.55 band.
_KNOWN = COSINE_KNOWN_THRESHOLD  # enter "known"
_MID = (IDENTITY_KNOWN_EXIT + COSINE_KNOWN_THRESHOLD) / 2  # inside hysteresis band
_BELOW_EXIT = IDENTITY_KNOWN_EXIT - 0.05  # below exit → downgrade


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
    """Once known, a dip inside the hysteresis band keeps the track 'known'."""
    p = _pipeline()
    assert p._resolve_tier(1, "Vlad", _KNOWN) == "known"
    # A sim between the exit and enter thresholds must NOT flip to uncertain.
    assert p._resolve_tier(1, "Vlad", _MID) == "known"
    assert p._resolve_tier(1, "Vlad", _MID) == "known"


def test_tier_downgrades_only_below_exit() -> None:
    p = _pipeline()
    assert p._resolve_tier(1, "Vlad", _KNOWN) == "known"
    # Below the exit threshold → drops to uncertain.
    assert p._resolve_tier(1, "Vlad", _BELOW_EXIT) == "uncertain"
    # From uncertain, a band-interior sim is NOT enough to re-enter known.
    assert p._resolve_tier(1, "Vlad", _MID) == "uncertain"
    # Clearing the full enter threshold upgrades again.
    assert p._resolve_tier(1, "Vlad", _KNOWN) == "known"


def test_tier_resets_on_winner_change() -> None:
    """A new winning name gets no sticky carryover from the previous identity."""
    p = _pipeline()
    assert p._resolve_tier(1, "Vlad", _KNOWN) == "known"
    # A band-interior sim for a different name must not inherit Vlad's stickiness.
    assert p._resolve_tier(1, "Anita", _MID) == "uncertain"


def test_resolve_identity_stable_label_under_flapping_sim() -> None:
    """End-to-end: a sim sequence that oscillates across the enter threshold but
    stays within the hysteresis band yields a single stable resolved string."""
    p = _pipeline()
    sims = [_KNOWN, _MID, _KNOWN, _MID, _MID, _KNOWN, _MID, _KNOWN]
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
    # A band-interior sim from a cold track is uncertain, not known.
    assert p._resolve_tier(99, "Vlad", _MID) == "uncertain"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
