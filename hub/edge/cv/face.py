"""ArcFace face recognition wrapper.

Extracts face embedding from person pose crop, compares against enrolled embeddings.
Throttle: 1 invocation per second per track_id.
T0 data: face frames stored encrypted on /mnt/edge-data/frames/.
"""

from __future__ import annotations

import math
import pickle
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from hailo_platform import HEF, VDevice  # type: ignore[import]  # noqa: F401

    HAILO_AVAILABLE = True
except ImportError:
    HAILO_AVAILABLE = False

COSINE_KNOWN_THRESHOLD = 0.6
COSINE_UNKNOWN_THRESHOLD = 0.4
T0_FRAME_DIR = Path("/mnt/edge-data/frames")


@dataclass
class RecognitionResult:
    track_id: int
    identity: str
    similarity: float
    embedding: list[float]


class FaceRecognizer:
    def __init__(
        self,
        hef_path: Path,
        embeddings_path: Path = Path("models/embeddings.pkl"),
    ) -> None:
        if not HAILO_AVAILABLE:
            raise ImportError("hailo_platform required")
        self._hef_path = hef_path
        self._embeddings_path = embeddings_path
        self._enrolled: dict[str, list[float]] = {}
        self._last_inference: dict[int, float] = {}

    def load(self) -> None:
        """Load HEF + enrolled embeddings from pkl."""
        if self._embeddings_path.exists():
            with open(self._embeddings_path, "rb") as f:
                self._enrolled = pickle.load(f)  # noqa: S301 — local T0 file

    def recognize(
        self,
        frame: Any,
        face_bbox: tuple[float, float, float, float],
        track_id: int,
    ) -> RecognitionResult | None:
        """Throttled recognition — returns None if called within 1s for same track_id."""
        now = time.monotonic()
        if now - self._last_inference.get(track_id, 0.0) < 1.0:
            return None
        self._last_inference[track_id] = now
        raise NotImplementedError("Hailo ArcFace pipeline — see Hailo Model Zoo")

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        na = math.sqrt(sum(x**2 for x in a))
        nb = math.sqrt(sum(x**2 for x in b))
        return dot / (na * nb + 1e-8) if na > 0 and nb > 0 else 0.0

    def save_t0_frame(self, frame: Any, track_id: int) -> Path | None:
        """Save frame to T0 encrypted storage if available."""
        if not T0_FRAME_DIR.exists():
            return None
        raise NotImplementedError("T0 storage implemented in T3.3")

    def close(self) -> None:
        pass
