"""Rule-based fall detection from pose keypoints.

Uses two signals:
1. Bounding-box aspect ratio: fallen person is wider than tall (ratio > threshold)
2. Spine angle: angle between hip midpoint and shoulder midpoint relative to vertical

Both signals must trigger for 3 consecutive frames to avoid false positives.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from hub.edge.cv.pose import Keypoints

_LEFT_SHOULDER = 5
_RIGHT_SHOULDER = 6
_LEFT_HIP = 11
_RIGHT_HIP = 12

BBOX_RATIO_THRESHOLD: float = 1.3
SPINE_ANGLE_THRESHOLD: float = 45.0
PERSISTENCE_FRAMES: int = 3


@dataclass
class FallEvent:
    track_id: int
    confidence: float
    bbox_ratio: float
    spine_angle_deg: float


class FallDetector:
    """Stateful per-track fall heuristic with persistence filter."""

    def __init__(self) -> None:
        self._history: dict[int, deque[tuple[bool, bool]]] = {}

    def update(
        self,
        track_id: int,
        keypoints: Keypoints,
        bbox: tuple[float, float, float, float],
    ) -> FallEvent | None:
        """Return FallEvent if fall detected for >= PERSISTENCE_FRAMES, else None."""
        ratio = self._bbox_ratio(bbox)
        angle = self._spine_angle(keypoints)

        bbox_triggered = ratio > BBOX_RATIO_THRESHOLD
        spine_triggered = angle > SPINE_ANGLE_THRESHOLD

        if track_id not in self._history:
            self._history[track_id] = deque(maxlen=PERSISTENCE_FRAMES)
        self._history[track_id].append((bbox_triggered, spine_triggered))

        history = self._history[track_id]
        if len(history) < PERSISTENCE_FRAMES:
            return None

        both_count = sum(1 for b, s in history if b and s)
        either_count = sum(1 for b, s in history if b or s)

        if both_count >= PERSISTENCE_FRAMES:
            confidence = 1.0
        elif either_count >= PERSISTENCE_FRAMES and both_count > 0:
            confidence = 0.5
        else:
            return None

        return FallEvent(
            track_id=track_id,
            confidence=confidence,
            bbox_ratio=ratio,
            spine_angle_deg=angle,
        )

    def _bbox_ratio(self, bbox: tuple[float, float, float, float]) -> float:
        """width / height of bounding box."""
        x1, y1, x2, y2 = bbox
        w, h = x2 - x1, y2 - y1
        return w / h if h > 0 else 0.0

    def _spine_angle(self, keypoints: Keypoints) -> float:
        """Angle of spine from vertical (0=upright, 90=horizontal)."""
        pts = keypoints.points
        if len(pts) <= max(_LEFT_HIP, _RIGHT_HIP):
            return 0.0
        sx = (pts[_LEFT_SHOULDER][0] + pts[_RIGHT_SHOULDER][0]) / 2
        sy = (pts[_LEFT_SHOULDER][1] + pts[_RIGHT_SHOULDER][1]) / 2
        hx = (pts[_LEFT_HIP][0] + pts[_RIGHT_HIP][0]) / 2
        hy = (pts[_LEFT_HIP][1] + pts[_RIGHT_HIP][1]) / 2
        dx, dy = sx - hx, sy - hy
        return abs(math.degrees(math.atan2(abs(dx), abs(dy) + 1e-6)))

    def clear_track(self, track_id: int) -> None:
        self._history.pop(track_id, None)
