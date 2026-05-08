"""Tests for the ObjectTracker / _SimpleIoUTracker."""

from __future__ import annotations

from hub.edge.cv.detector import Detection
from hub.edge.cv.tracker import ObjectTracker


def _det(x1: float, y1: float, x2: float, y2: float, label: str = "person") -> Detection:
    return Detection(class_id=0, label=label, confidence=0.9, bbox=(x1, y1, x2, y2))


def test_empty_detections_no_tracks() -> None:
    tracker = ObjectTracker()
    tracks = tracker.update([], (480, 640))
    assert tracks == []
    assert tracker.active_track_count == 0


def test_single_detection_assigns_track_id() -> None:
    tracker = ObjectTracker()
    det = _det(0.1, 0.1, 0.5, 0.5)
    tracks = tracker.update([det], (480, 640))
    assert len(tracks) == 1
    assert tracks[0].track_id >= 1


def test_same_bbox_next_frame_same_track_id() -> None:
    tracker = ObjectTracker()
    det = _det(0.1, 0.1, 0.5, 0.5)
    tracks1 = tracker.update([det], (480, 640))
    assert len(tracks1) == 1
    first_id = tracks1[0].track_id

    tracks2 = tracker.update([det], (480, 640))
    assert len(tracks2) == 1
    assert tracks2[0].track_id == first_id


def test_detection_gone_31_frames_track_removed() -> None:
    tracker = ObjectTracker()
    det = _det(0.1, 0.1, 0.5, 0.5)
    tracker.update([det], (480, 640))

    for _ in range(31):
        tracks = tracker.update([], (480, 640))

    assert tracks == []
    assert tracker.active_track_count == 0


def test_two_separate_detections_two_track_ids() -> None:
    tracker = ObjectTracker()
    det_a = _det(0.0, 0.0, 0.2, 0.2)
    det_b = _det(0.7, 0.7, 0.9, 0.9)
    tracks = tracker.update([det_a, det_b], (480, 640))
    assert len(tracks) == 2
    ids = {t.track_id for t in tracks}
    assert len(ids) == 2
