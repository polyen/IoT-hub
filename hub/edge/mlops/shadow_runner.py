"""Shadow CV runner — evaluates candidate models without publishing alerts."""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from hub.edge.cv.detector import HailoDetector

    DETECTOR_AVAILABLE = True
except ImportError:
    HailoDetector = None  # type: ignore[assignment,misc]
    DETECTOR_AVAILABLE = False

try:
    import cv2

    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    from prometheus_client import Counter, Gauge, Histogram, start_http_server

    PROM_AVAILABLE = True
except ImportError:  # pragma: no cover
    PROM_AVAILABLE = False

# ---------------------------------------------------------------------------
# Prometheus metrics (registered lazily so they don't clash in unit tests)
# ---------------------------------------------------------------------------
_SHADOW_FPS: Any = None
_SHADOW_DETECTIONS: Any = None
_SHADOW_INFERENCE_MS: Any = None


def _init_metrics() -> None:
    global _SHADOW_FPS, _SHADOW_DETECTIONS, _SHADOW_INFERENCE_MS
    if not PROM_AVAILABLE or _SHADOW_FPS is not None:
        return
    _SHADOW_FPS = Gauge(
        "iot_hub_shadow_fps",
        "Shadow pipeline FPS",
        ["model_version"],
    )
    _SHADOW_DETECTIONS = Counter(
        "iot_hub_shadow_detections_total",
        "Shadow detections",
        ["model_version", "class_name"],
    )
    _SHADOW_INFERENCE_MS = Histogram(
        "iot_hub_shadow_inference_ms",
        "Shadow inference ms",
        ["model_version"],
        buckets=[5, 10, 20, 30, 50, 75, 100],
    )


@dataclass
class ShadowMetrics:
    model_version: str
    detections_total: int = 0
    fps_sum: float = 0.0
    frame_count: int = 0
    inference_ms_sum: float = 0.0


class ShadowRunner:
    """Runs a candidate model against the live RTSP stream without publishing MQTT alerts."""

    def __init__(
        self,
        rtsp_url: str,
        model_path: Path,
        model_version: str,
        metrics_port: int = 8003,
    ) -> None:
        self.rtsp_url = rtsp_url
        self.model_path = model_path
        self.model_version = model_version
        self.metrics_port = metrics_port
        self.metrics = ShadowMetrics(model_version=model_version)
        self._running = False

        _init_metrics()

    async def run(self) -> None:
        """Open RTSP stream, run inference, update Prometheus metrics. Never publishes MQTT."""
        if not CV2_AVAILABLE:
            raise ImportError("opencv-python not installed — cannot open RTSP stream")
        if not DETECTOR_AVAILABLE or HailoDetector is None:
            raise ImportError(
                "hailo_platform not installed — ShadowRunner requires HailoDetector. "
                "Run on RPi5 with HailoRT."
            )

        detector = HailoDetector(self.model_path)
        detector.load()
        self._running = True

        cap: Any = cv2.VideoCapture(self.rtsp_url)
        if not cap.isOpened():
            detector.close()
            raise RuntimeError(f"Cannot open RTSP stream: {self.rtsp_url}")

        fps_window_start = time.monotonic()
        fps_frame_count = 0
        last_log = time.monotonic()

        try:
            while self._running:
                ok, frame = await asyncio.get_event_loop().run_in_executor(None, cap.read)
                if not ok:
                    logger.warning("Shadow: frame read failed — retrying in 1s")
                    await asyncio.sleep(1)
                    continue

                t0 = time.monotonic()
                detections = await asyncio.get_event_loop().run_in_executor(
                    None, detector.detect, frame
                )
                inference_ms = (time.monotonic() - t0) * 1000

                # Update in-memory metrics
                self.metrics.frame_count += 1
                self.metrics.inference_ms_sum += inference_ms
                self.metrics.detections_total += len(detections)

                fps_frame_count += 1
                elapsed = time.monotonic() - fps_window_start
                if elapsed >= 1.0:
                    current_fps = fps_frame_count / elapsed
                    self.metrics.fps_sum += current_fps
                    if _SHADOW_FPS is not None:
                        _SHADOW_FPS.labels(model_version=self.model_version).set(current_fps)
                    fps_frame_count = 0
                    fps_window_start = time.monotonic()

                for det in detections:
                    if _SHADOW_DETECTIONS is not None:
                        _SHADOW_DETECTIONS.labels(
                            model_version=self.model_version,
                            class_name=det.label,
                        ).inc()
                if _SHADOW_INFERENCE_MS is not None:
                    _SHADOW_INFERENCE_MS.labels(model_version=self.model_version).observe(
                        inference_ms
                    )

                # Log summary every 30 s
                now = time.monotonic()
                if now - last_log >= 30:
                    avg_fps = (
                        self.metrics.fps_sum / max(1, self.metrics.frame_count)
                        if self.metrics.frame_count
                        else 0.0
                    )
                    logger.info(
                        "Shadow[%s] frames=%d detections=%d avg_fps=%.1f",
                        self.model_version,
                        self.metrics.frame_count,
                        self.metrics.detections_total,
                        avg_fps,
                    )
                    last_log = now

                await asyncio.sleep(0)  # yield to event loop

        finally:
            cap.release()
            detector.close()
            self._running = False

    async def compare_with_active(self, active_event_ids: list[str]) -> dict[str, Any]:
        """Compare shadow vs active model detection rates via Prometheus.

        Queries the 1-hour detection rate for both shadow (by model_version label)
        and active (all detections) models. Falls back to raw detection counts
        when Prometheus is unavailable.
        """
        from hub.edge.mlops.deploy import _PROMETHEUS_URL, _prom_scalar

        shadow_fps = (
            self.metrics.fps_sum / max(1, self.metrics.frame_count)
            if self.metrics.frame_count
            else 0.0
        )
        shadow_det_rate = (
            self.metrics.detections_total / max(1, self.metrics.frame_count)
            if self.metrics.frame_count
            else 0.0
        )

        prom_data: dict[str, Any] = {}
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5) as client:
                shadow_rate = await _prom_scalar(
                    client,
                    _PROMETHEUS_URL,
                    f'rate(iot_hub_shadow_detections_total{{model_version="{self.model_version}"}}[1h])',
                )
                active_rate = await _prom_scalar(
                    client,
                    _PROMETHEUS_URL,
                    "rate(iot_hub_cv_detections_total[1h])",
                )
            prom_data = {
                "shadow_rate_1h": shadow_rate,
                "active_rate_1h": active_rate,
                "rate_ratio": round(shadow_rate / active_rate, 3) if active_rate > 0 else None,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "compare_with_active: Prometheus unavailable (%s) — using counters only", exc
            )

        return {
            "shadow_version": self.model_version,
            "active_events_sampled": len(active_event_ids),
            "shadow_frames": self.metrics.frame_count,
            "shadow_detections_total": self.metrics.detections_total,
            "shadow_det_per_frame": round(shadow_det_rate, 4),
            "shadow_avg_fps": round(shadow_fps, 1),
            "shadow_avg_inference_ms": round(
                self.metrics.inference_ms_sum / max(1, self.metrics.frame_count), 1
            ),
            **prom_data,
        }

    def stop(self) -> None:
        """Signal the run loop to exit after the current frame."""
        self._running = False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="Shadow CV runner")
    parser.add_argument("--rtsp-url", required=True, help="RTSP stream URL")
    parser.add_argument("--model", required=True, type=Path, help="Path to .hef model file")
    parser.add_argument("--model-version", required=True, help="Version label (e.g. v2)")
    parser.add_argument(
        "--metrics-port", type=int, default=8003, help="Prometheus metrics HTTP port"
    )
    args = parser.parse_args()

    if PROM_AVAILABLE:
        start_http_server(args.metrics_port)

    runner = ShadowRunner(
        rtsp_url=args.rtsp_url,
        model_path=args.model,
        model_version=args.model_version,
        metrics_port=args.metrics_port,
    )
    asyncio.run(runner.run())
