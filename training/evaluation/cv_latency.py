"""CV cascade FPS and inference latency profiler."""

from __future__ import annotations

import argparse
import json
import logging
import statistics
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
    from ultralytics import YOLO  # type: ignore[attr-defined]

    ULTRALYTICS_AVAILABLE = True
except ImportError:
    ULTRALYTICS_AVAILABLE = False

try:
    import hailo_platform  # noqa: F401

    HAILO_AVAILABLE = True
except ImportError:
    HAILO_AVAILABLE = False


def _not_measured(note: str) -> dict[str, Any]:
    return {"measured": False, "fps_mean": None, "pass": None, "note": note}


class LatencyProfiler:
    """Profiles inference latency and FPS for CV cascade pipeline."""

    def __init__(
        self,
        rtsp_url: str = "",
        n_frames: int = 300,
        warm_up: int = 30,
    ) -> None:
        self.rtsp_url = rtsp_url
        self.n_frames = n_frames
        self.warm_up = warm_up

    def _open_capture(self) -> Any:
        """Open video capture from RTSP URL or return None."""
        if not CV2_AVAILABLE:
            return None
        cap = cv2.VideoCapture(self.rtsp_url)
        if not cap.isOpened():
            logger.warning("Could not open RTSP stream: %s", self.rtsp_url)
            return None
        return cap

    def profile_cpu_only(self, model_path: str) -> dict[str, Any]:
        """Profile CPU-only inference: frame decode + YOLO predict."""
        if not ULTRALYTICS_AVAILABLE or not CV2_AVAILABLE:
            return _not_measured("ultralytics or cv2 not installed")
        if not model_path:
            return _not_measured("--model is required for CPU profiling")

        model = YOLO(model_path)
        cap = self._open_capture()
        if cap is None:
            return _not_measured(f"RTSP stream unavailable: {self.rtsp_url!r}")

        frame_times: list[float] = []
        inference_times: list[float] = []
        total = self.n_frames + self.warm_up
        collected = 0

        while collected < total:
            ret, frame = cap.read()
            if not ret:
                break
            t0 = time.perf_counter()
            model.predict(frame, verbose=False, device="cpu")
            t1 = time.perf_counter()
            if collected >= self.warm_up:
                elapsed = t1 - t0
                frame_times.append(elapsed)
                inference_times.append(elapsed * 1000)
            collected += 1

        cap.release()

        if not frame_times:
            return _not_measured("no frames decoded from the stream")

        fps_list = [1.0 / t for t in frame_times]
        return {
            "measured": True,
            "fps_mean": round(statistics.mean(fps_list), 2),
            "fps_p5": round(sorted(fps_list)[max(0, int(len(fps_list) * 0.05))], 2),
            "inference_ms_mean": round(statistics.mean(inference_times), 2),
            "inference_ms_p95": round(sorted(inference_times)[int(len(inference_times) * 0.95)], 2),
            "device": "cpu",
            "n_frames": len(frame_times),
        }

    def profile_hailo(self, model_path: str) -> dict[str, Any]:
        """On-NPU FPS is measured by ``cv_detector_compare`` (real ``detect()``).

        This profiler never had a real Hailo inference path — the previous
        implementation timed an empty loop, so it is intentionally not provided
        here rather than reporting a fabricated number.
        """
        return _not_measured(
            "on-NPU FPS is measured by `make evaluate-cv-compare` "
            "(training.evaluation.cv_detector_compare), which runs the real HailoDetector"
        )

    def run(
        self,
        rtsp_url: str = "",
        model_path: str = "",
        device: str = "cpu",
    ) -> dict[str, Any]:
        """Run latency profile for given device and return combined metrics."""
        if rtsp_url:
            self.rtsp_url = rtsp_url

        if device == "hailo":
            result = self.profile_hailo(model_path)
        else:
            result = self.profile_cpu_only(model_path)

        result["target_fps"] = 15.0
        if result.get("measured"):
            result["pass"] = result.get("fps_mean", 0.0) > 15.0
        else:
            result["pass"] = None
        return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="CV cascade FPS and latency profiler")
    parser.add_argument("--rtsp-url", default="", help="RTSP stream URL")
    parser.add_argument("--model", default="", help="Model path (.pt or .hef)")
    parser.add_argument("--device", default="cpu", choices=["cpu", "hailo"])
    parser.add_argument("--n-frames", type=int, default=300)
    parser.add_argument("--output", default="materials/evaluation_results")
    args = parser.parse_args()

    profiler = LatencyProfiler(
        rtsp_url=args.rtsp_url,
        n_frames=args.n_frames,
    )
    result = profiler.run(rtsp_url=args.rtsp_url, model_path=args.model, device=args.device)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "cv_latency.json"
    with open(out_file, "w") as f:
        json.dump(result, f, indent=2)

    logger.info("Results written to %s", out_file)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
