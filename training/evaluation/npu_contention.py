"""NPU contention benchmark — empirical backing for Contribution #3.

The thesis claims the Hailo-8 is dedicated to the CV cascade and STT runs on the
CPU *specifically to avoid NPU contention* (§4.2.2, §4.3.4). This script
quantifies the cost of the **rejected** configuration — STT on the NPU alongside
CV — by measuring CV detector throughput in two regimes:

  1. **baseline** — CV detector alone on the NPU.
  2. **contended** — CV detector while a Hailo Whisper STT loop hammers the same
     NPU from a background thread.

``degradation_pct = (baseline_fps - contended_fps) / baseline_fps`` is the
number that justifies keeping STT off the NPU. The *production* config (STT on
CPU) has zero contention by construction, so this measures what was avoided.

Requires real hardware (``hailo_platform`` + a detector HEF + the Hailo Whisper
assets). On any other machine it returns ``{"measured": false, ...}`` with a
note — it never fabricates a degradation figure.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import struct
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import cv2

    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    import hailo_platform  # noqa: F401

    HAILO_AVAILABLE = True
except ImportError:
    HAILO_AVAILABLE = False

SAMPLE_RATE = 16000


def _not_measured(note: str) -> dict[str, Any]:
    return {"measured": False, "pass": None, "note": note}


def _sine_wav_bytes(duration_s: int = 5) -> bytes:
    """Minimal 16 kHz mono sine WAV (the STT payload need not be meaningful here)."""
    import math

    n = duration_s * SAMPLE_RATE
    raw = struct.pack(
        f"<{n}h", *(int(16000 * math.sin(2 * math.pi * 440 * i / SAMPLE_RATE)) for i in range(n))
    )
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(raw),
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        SAMPLE_RATE,
        SAMPLE_RATE * 2,
        2,
        16,
        b"data",
        len(raw),
    )
    return header + raw


def _measure_cv_fps(detector: Any, frames: list[Any], warmup: int) -> dict[str, float]:
    """Run detect() over frames (cycled) and return FPS + latency percentiles."""
    latencies: list[float] = []
    n_total = len(frames)
    for i in range(n_total):
        frame = frames[i % len(frames)]
        t0 = time.perf_counter()
        detector.detect(frame)
        dt = time.perf_counter() - t0
        if i >= warmup:
            latencies.append(dt)
    mean = statistics.mean(latencies)
    s = sorted(latencies)
    return {
        "fps_mean": round(1.0 / mean, 2),
        "latency_p50_ms": round(s[len(s) // 2] * 1000, 2),
        "latency_p95_ms": round(s[max(0, int(len(s) * 0.95) - 1)] * 1000, 2),
        "n_frames": len(latencies),
    }


def _load_frames(images_dir: Path, n: int) -> list[Any]:
    paths = sorted(images_dir.glob("*.jpg"))[:n]
    frames = [cv2.imread(str(p)) for p in paths]
    return [f for f in frames if f is not None]


class _STTLoad:
    """Background thread that keeps a Hailo Whisper STT loop busy on the NPU."""

    def __init__(self, backend: Any, audio: bytes) -> None:
        self._backend = backend
        self._audio = audio
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.iterations = 0

    def _run(self) -> None:
        import asyncio

        loop = asyncio.new_event_loop()
        while not self._stop.is_set():
            loop.run_until_complete(self._backend.transcribe(self._audio))
            self.iterations += 1
        loop.close()

    def __enter__(self) -> _STTLoad:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=10)


def run(hef: Path, images_dir: Path, *, n_frames: int, warmup: int) -> dict[str, Any]:
    if not HAILO_AVAILABLE:
        return _not_measured("hailo_platform not installed — run on the RPi 5 + Hailo-8")
    if not CV2_AVAILABLE:
        return _not_measured("opencv-python (cv2) not installed")
    if not hef.exists():
        return _not_measured(f"detector HEF not found: {hef}")
    if not images_dir.exists():
        return _not_measured(f"frame source dir not found: {images_dir}")

    frames = _load_frames(images_dir, n_frames + warmup)
    if not frames:
        return _not_measured(f"no .jpg frames in {images_dir}")

    # Force the opt-in NPU STT backend; the contention scenario requires STT to
    # actually run on the Hailo-8, not fall back to CPU faster-whisper.
    os.environ["STT_BACKEND"] = "hailo"
    from hub.edge.voice.hailo_whisper import HailoWhisperBackend, get_backend

    stt_backend = get_backend(force_cpu=False)
    if not isinstance(stt_backend, HailoWhisperBackend):
        return _not_measured(
            f"STT did not land on the NPU (got {type(stt_backend).__name__}); "
            "Hailo Whisper assets/transformers likely missing"
        )

    from hub.edge.cv.detector import HailoDetector

    detector = HailoDetector(hef)
    detector.load(scheduled=True)
    try:
        baseline = _measure_cv_fps(detector, frames, warmup)
        with _STTLoad(stt_backend, _sine_wav_bytes()):
            contended = _measure_cv_fps(detector, frames, warmup)
    finally:
        detector.close()

    base_fps = baseline["fps_mean"]
    cont_fps = contended["fps_mean"]
    degradation = (base_fps - cont_fps) / base_fps if base_fps > 0 else 0.0

    return {
        "measured": True,
        "baseline": baseline,
        "contended": contended,
        "degradation_pct": round(degradation * 100, 1),
        "cv_fps_target": 15.0,
        "contended_meets_target": cont_fps >= 15.0,
        "note": (
            "Production runs STT on CPU → zero contention; this quantifies the "
            "rejected NPU-shared configuration."
        ),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="NPU contention: CV alone vs CV + STT-on-NPU")
    parser.add_argument("--hef", required=True, help="Detector HEF path")
    parser.add_argument(
        "--frames",
        default="datasets/fire_smoke_mixed/test/images",
        help="Directory of .jpg frames to feed the detector",
    )
    parser.add_argument("--n-frames", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--output", default="materials/evaluation_results")
    args = parser.parse_args()

    result = run(
        Path(args.hef),
        Path(args.frames),
        n_frames=args.n_frames,
        warmup=args.warmup,
    )

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "npu_contention.json").write_text(json.dumps(result, indent=2))

    logger.info("Results written to %s", out_dir / "npu_contention.json")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
