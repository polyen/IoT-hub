"""Tests for FallDetector rule-based fall heuristic."""

from __future__ import annotations

from hub.edge.cv.fall_rule import PERSISTENCE_FRAMES, FallDetector, FallEvent
from hub.edge.cv.pose import Keypoints

_NUM_KPS = 17


def _make_kps(
    shoulder_x: float = 0.5,
    shoulder_y: float = 0.2,
    hip_x: float = 0.5,
    hip_y: float = 0.6,
) -> Keypoints:
    """Build 17-keypoint Keypoints with specific shoulder/hip positions."""
    pts: list[tuple[float, float, float]] = [(0.5, 0.5, 0.9)] * _NUM_KPS
    pts[5] = (shoulder_x - 0.05, shoulder_y, 0.9)
    pts[6] = (shoulder_x + 0.05, shoulder_y, 0.9)
    pts[11] = (hip_x - 0.05, hip_y, 0.9)
    pts[12] = (hip_x + 0.05, hip_y, 0.9)
    return Keypoints(points=pts, track_id=1)


def _horizontal_kps() -> Keypoints:
    """Keypoints for a fallen person — spine nearly horizontal."""
    return _make_kps(shoulder_x=0.8, shoulder_y=0.5, hip_x=0.2, hip_y=0.5)


def _standing_kps() -> Keypoints:
    """Keypoints for a standing person — spine nearly vertical."""
    return _make_kps(shoulder_x=0.5, shoulder_y=0.2, hip_x=0.5, hip_y=0.6)


_FALLEN_BBOX = (0.1, 0.4, 0.9, 0.6)  # wide (w=0.8, h=0.2) → ratio=4.0
_STANDING_BBOX = (0.4, 0.1, 0.6, 0.9)  # tall (w=0.2, h=0.8) → ratio=0.25
_SQUAT_BBOX = (0.1, 0.4, 0.9, 0.6)  # wide bbox but will pair with upright spine


def test_fall_both_signals_3_frames_returns_event() -> None:
    detector = FallDetector()
    kps = _horizontal_kps()
    result = None
    for _ in range(PERSISTENCE_FRAMES):
        result = detector.update(1, kps, _FALLEN_BBOX)
    assert result is not None
    assert isinstance(result, FallEvent)
    assert result.track_id == 1


def test_fall_only_2_frames_returns_none() -> None:
    detector = FallDetector()
    kps = _horizontal_kps()
    result = None
    for _ in range(PERSISTENCE_FRAMES - 1):
        result = detector.update(1, kps, _FALLEN_BBOX)
    assert result is None


def test_standing_pose_returns_none() -> None:
    detector = FallDetector()
    kps = _standing_kps()
    result = None
    for _ in range(PERSISTENCE_FRAMES + 2):
        result = detector.update(1, kps, _STANDING_BBOX)
    assert result is None


def test_wide_bbox_upright_spine_not_fall() -> None:
    """Wide bounding box but spine is vertical — not a fall."""
    detector = FallDetector()
    kps = _standing_kps()
    result = None
    for _ in range(PERSISTENCE_FRAMES + 2):
        result = detector.update(1, kps, _SQUAT_BBOX)
    assert result is None


def test_confidence_both_signals_is_1_one_signal_is_half() -> None:
    detector_both = FallDetector()
    kps_fallen = _horizontal_kps()
    event_both = None
    for _ in range(PERSISTENCE_FRAMES):
        event_both = detector_both.update(1, kps_fallen, _FALLEN_BBOX)
    assert event_both is not None
    assert event_both.confidence == 1.0

    # Mixed sequence: first frame both signals fire, subsequent frames only bbox fires.
    # This yields either_count >= PERSISTENCE_FRAMES and both_count == 1 → confidence 0.5.
    detector_mixed = FallDetector()
    event_mixed = None
    event_mixed = detector_mixed.update(3, _horizontal_kps(), _FALLEN_BBOX)  # both
    for _ in range(PERSISTENCE_FRAMES - 1):
        event_mixed = detector_mixed.update(3, _standing_kps(), _FALLEN_BBOX)  # bbox only
    assert event_mixed is not None
    assert event_mixed.confidence == 0.5
