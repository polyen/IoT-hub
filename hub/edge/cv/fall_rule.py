"""Rule-based fall detection from pose keypoints.

Uses two signals:
1. Bounding-box aspect ratio: fallen person is wider than tall (ratio > threshold)
2. Spine angle: angle between hip midpoint and shoulder midpoint relative to vertical

Both signals must trigger for 3 consecutive frames to avoid false positives.

Coordinates note: keypoints and bbox arrive in **frame-normalised** ``[0, 1]``
space — divided by frame width/height respectively.  On a non-square frame
(typical RTSP is 1920×1080 = 16:9) the X axis is therefore compressed
relative to Y in normalised units.  Without correction the spine angle and
bbox ratio under-report by a factor of ``H/W`` ≈ 0.56, so the 45° / 1.3
thresholds end up needing a real-world lean of ~62° and a real-world width
ratio of ~2.3 — strict enough to miss mid-falls.  ``update`` accepts a
``frame_aspect = W / H`` argument; pass it from the pipeline so both
signals are computed in pixel-equivalent space and the thresholds fire at
their nominal real-world values.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass

from hub.edge.cv.pose import Keypoints

logger = logging.getLogger(__name__)

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
        self._diag_count: dict[int, int] = {}
        self._diag_every: int = 30

    def update(
        self,
        track_id: int,
        keypoints: Keypoints,
        bbox: tuple[float, float, float, float],
        frame_aspect: float = 1.0,
    ) -> FallEvent | None:
        """Return FallEvent if fall detected for >= PERSISTENCE_FRAMES, else None.

        ``frame_aspect`` is ``W / H`` of the source frame.  Default 1.0 keeps
        backwards-compatible (square-frame) behaviour for unit tests; the
        pipeline should pass the real camera aspect so the thresholds match
        their real-world definitions.
        """
        ratio = self._bbox_ratio(bbox, frame_aspect)
        angle = self._spine_angle(keypoints, frame_aspect)

        bbox_triggered = ratio > BBOX_RATIO_THRESHOLD
        spine_triggered = angle > SPINE_ANGLE_THRESHOLD

        if track_id not in self._history:
            self._history[track_id] = deque(maxlen=PERSISTENCE_FRAMES)
        self._history[track_id].append((bbox_triggered, spine_triggered))

        history = self._history[track_id]

        self._diag_count[track_id] = self._diag_count.get(track_id, 0) + 1
        if self._diag_count[track_id] % self._diag_every == 1:
            logger.info(
                "FallDetector track=%d ratio=%.2f (>%.2f? %s) "
                "spine=%.1f° (>%.1f? %s) aspect=%.2f hist=%s",
                track_id,
                ratio,
                BBOX_RATIO_THRESHOLD,
                bbox_triggered,
                angle,
                SPINE_ANGLE_THRESHOLD,
                spine_triggered,
                frame_aspect,
                list(history),
            )

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

    def _bbox_ratio(
        self, bbox: tuple[float, float, float, float], frame_aspect: float = 1.0
    ) -> float:
        """Pixel-space width / height of the bbox.

        Inputs are frame-normalised; multiplying ``w_norm / h_norm`` by
        ``W/H`` recovers the real-pixel aspect ratio.
        """
        x1, y1, x2, y2 = bbox
        w_norm, h_norm = x2 - x1, y2 - y1
        if h_norm <= 0:
            return 0.0
        return (w_norm / h_norm) * frame_aspect

    def _spine_angle(self, keypoints: Keypoints, frame_aspect: float = 1.0) -> float:
        """Angle of spine from vertical (0=upright, 90=horizontal) in pixel space."""
        pts = keypoints.points
        if len(pts) <= max(_LEFT_HIP, _RIGHT_HIP):
            return 0.0
        sx = (pts[_LEFT_SHOULDER][0] + pts[_RIGHT_SHOULDER][0]) / 2
        sy = (pts[_LEFT_SHOULDER][1] + pts[_RIGHT_SHOULDER][1]) / 2
        hx = (pts[_LEFT_HIP][0] + pts[_RIGHT_HIP][0]) / 2
        hy = (pts[_LEFT_HIP][1] + pts[_RIGHT_HIP][1]) / 2
        dx = (sx - hx) * frame_aspect
        dy = sy - hy
        return abs(math.degrees(math.atan2(abs(dx), abs(dy) + 1e-6)))

    def clear_track(self, track_id: int) -> None:
        self._history.pop(track_id, None)
        self._diag_count.pop(track_id, None)
