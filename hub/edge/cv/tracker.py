"""ByteTrack wrapper for multi-object tracking.

Uses bytetracker library when available; falls back to a minimal IoU tracker.
The interface is identical in both cases.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

try:
    from bytetracker import BYTETracker  # type: ignore[import]

    BYTE_TRACKER_AVAILABLE = True
except ImportError:
    BYTE_TRACKER_AVAILABLE = False

try:
    import numpy as np  # type: ignore[import]

    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

try:
    from prometheus_client import Gauge

    PROM_AVAILABLE = True
except ImportError:
    PROM_AVAILABLE = False

from hub.edge.cv.detector import Detection

if PROM_AVAILABLE:
    ACTIVE_TRACKS_GAUGE: Any = Gauge(
        "iot_hub_cv_active_tracks", "Number of currently active tracks"
    )
else:
    ACTIVE_TRACKS_GAUGE = None


@dataclass
class Track:
    track_id: int
    detection: Detection
    age: int = 0
    is_confirmed: bool = True


@dataclass
class _IoUTrack:
    track_id: int
    bbox: tuple[float, float, float, float]
    last_seen: float = field(default_factory=time.monotonic)
    age_frames: int = 0


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


class _SimpleIoUTracker:
    """Minimal IoU-based fallback tracker."""

    _IOU_THRESHOLD = 0.3

    def __init__(self, max_lost_frames: int = 30) -> None:
        self._max_lost = max_lost_frames
        self._tracks: list[_IoUTrack] = []
        self._next_id = 1

    def update(self, detections: list[Detection]) -> list[tuple[int, Detection]]:
        """Match detections to tracks; return list of (track_id, detection)."""
        pairs: list[tuple[float, int, int]] = []
        for di, det in enumerate(detections):
            for ti, trk in enumerate(self._tracks):
                score = _iou(det.bbox, trk.bbox)
                if score >= self._IOU_THRESHOLD:
                    pairs.append((score, di, ti))

        pairs.sort(key=lambda x: x[0], reverse=True)

        det_matched: set[int] = set()
        trk_matched: set[int] = set()
        matched_track_ids: set[int] = set()

        for _score, di, ti in pairs:
            if di in det_matched or ti in trk_matched:
                continue
            self._tracks[ti].bbox = detections[di].bbox
            self._tracks[ti].age_frames = 0
            det_matched.add(di)
            trk_matched.add(ti)
            matched_track_ids.add(self._tracks[ti].track_id)

        for di in range(len(detections)):
            if di not in det_matched:
                self._tracks.append(
                    _IoUTrack(
                        track_id=self._next_id,
                        bbox=detections[di].bbox,
                    )
                )
                self._next_id += 1

        for trk in self._tracks:
            if trk.track_id not in matched_track_ids:
                trk.age_frames += 1

        self._tracks = [t for t in self._tracks if t.age_frames <= self._max_lost]

        det_to_track: dict[int, int] = {}
        pairs2: list[tuple[float, int, int]] = []
        for di, det in enumerate(detections):
            for ti, trk in enumerate(self._tracks):
                score = _iou(det.bbox, trk.bbox)
                if score >= self._IOU_THRESHOLD:
                    pairs2.append((score, di, ti))
        pairs2.sort(key=lambda x: x[0], reverse=True)
        used_di: set[int] = set()
        used_ti: set[int] = set()
        for _score, di, ti in pairs2:
            if di in used_di or ti in used_ti:
                continue
            det_to_track[di] = self._tracks[ti].track_id
            used_di.add(di)
            used_ti.add(ti)

        results: list[tuple[int, Detection]] = []
        for di, det in enumerate(detections):
            if di in det_to_track:
                results.append((det_to_track[di], det))
            else:
                for trk in self._tracks:
                    if trk.bbox == det.bbox and trk.age_frames == 0:
                        results.append((trk.track_id, det))
                        break

        return results

    @property
    def active_count(self) -> int:
        return len(self._tracks)


class ObjectTracker:
    """Public tracker API — wraps BYTETracker or falls back to IoU tracker."""

    def __init__(self, max_lost_frames: int = 30) -> None:
        self._max_lost = max_lost_frames
        if BYTE_TRACKER_AVAILABLE and NUMPY_AVAILABLE:
            self._byte_tracker: Any = BYTETracker(
                track_thresh=0.25,
                track_buffer=max_lost_frames,
                match_thresh=0.8,
                frame_rate=15,
            )
            self._use_byte = True
        else:
            self._iou_tracker = _SimpleIoUTracker(max_lost_frames)
            self._use_byte = False
        self._track_count = 0

    def update(self, detections: list[Detection], frame_shape: tuple[int, int]) -> list[Track]:
        """Update tracks with new detections. Returns active tracks."""
        if not detections:
            if not self._use_byte:
                self._iou_tracker.update([])
            self._track_count = 0
            if ACTIVE_TRACKS_GAUGE is not None:
                ACTIVE_TRACKS_GAUGE.set(0)
            return []

        if self._use_byte:
            tracks = self._update_byte(detections, frame_shape)
        else:
            tracks = self._update_iou(detections)

        self._track_count = len(tracks)
        if ACTIVE_TRACKS_GAUGE is not None:
            ACTIVE_TRACKS_GAUGE.set(self._track_count)
        return tracks

    def _update_byte(
        self, detections: list[Detection], frame_shape: tuple[int, int]
    ) -> list[Track]:
        h, w = frame_shape
        dets_np = np.array(
            [
                [
                    d.bbox[0] * w,
                    d.bbox[1] * h,
                    d.bbox[2] * w,
                    d.bbox[3] * h,
                    d.confidence,
                ]
                for d in detections
            ],
            dtype=np.float32,
        )
        online_targets = self._byte_tracker.update(dets_np, frame_shape, frame_shape)
        tracks: list[Track] = []
        for target in online_targets:
            tlbr = target.tlbr
            bbox = (
                tlbr[0] / w,
                tlbr[1] / h,
                tlbr[2] / w,
                tlbr[3] / h,
            )
            best_det = min(detections, key=lambda d, b=bbox: abs(d.bbox[0] - b[0]))  # type: ignore[misc]
            det = Detection(
                class_id=best_det.class_id,
                label=best_det.label,
                confidence=best_det.confidence,
                bbox=bbox,
            )
            tracks.append(Track(track_id=int(target.track_id), detection=det))
        return tracks

    def _update_iou(self, detections: list[Detection]) -> list[Track]:
        pairs = self._iou_tracker.update(detections)
        return [Track(track_id=tid, detection=det) for tid, det in pairs]

    @property
    def active_track_count(self) -> int:
        return self._track_count
