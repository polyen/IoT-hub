"""Pose estimation wrapper — runs YOLO26n-pose on person crops.

Only invoked on tracked person detections (one per track, throttled).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from hailo_platform import HEF, VDevice  # type: ignore[import]  # noqa: F401

    HAILO_AVAILABLE = True
except ImportError:
    HAILO_AVAILABLE = False


@dataclass
class Keypoints:
    """17 COCO keypoints as normalized (x, y, confidence) tuples."""

    points: list[tuple[float, float, float]]  # 17 points: (x, y, conf)
    track_id: int


class PoseEstimator:
    """Runs YOLO26n-pose on person crops via Hailo NPU."""

    def __init__(self, hef_path: Path) -> None:
        if not HAILO_AVAILABLE:
            raise ImportError("hailo_platform required — run on RPi5 with HailoRT")
        self._hef_path = hef_path
        self._loaded = False

    def load(self) -> None:
        """Load HEF and initialize Hailo device."""
        self._loaded = True

    def estimate(self, frame: Any, bbox: tuple[float, float, float, float]) -> Keypoints | None:
        """Extract pose from person crop. Returns None if no person detected."""
        if not self._loaded:
            raise RuntimeError("Call load() first")
        raise NotImplementedError("Hailo pose pipeline — see hailo-rpi5-examples/pose_estimation")

    def close(self) -> None:
        self._loaded = False
