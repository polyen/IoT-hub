"""Async RTSP capture -> Hailo detector -> MQTT publish loop.

Target: 15 FPS sustained, <5% dropped frames.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from hub.edge.cv.detector import Detection, HailoDetector
from hub.edge.cv.tracker import ObjectTracker, Track

logger = logging.getLogger(__name__)

try:
    import cv2

    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    import aiomqtt

    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False

try:
    from prometheus_client import Counter, Gauge, Histogram

    PROM_AVAILABLE = True
except ImportError:
    PROM_AVAILABLE = False

if PROM_AVAILABLE:
    FPS_GAUGE: Any = Gauge("iot_hub_cv_fps", "Current CV pipeline FPS")
    INFERENCE_HIST: Any = Histogram(
        "iot_hub_cv_inference_ms",
        "Hailo inference latency in ms",
        buckets=[5, 10, 20, 30, 50, 75, 100],
    )
    DETECTIONS_COUNTER: Any = Counter(
        "iot_hub_cv_detections_total",
        "Total detections published",
        ["label"],
    )
else:
    FPS_GAUGE = INFERENCE_HIST = DETECTIONS_COUNTER = None


async def _capture_frames(rtsp_url: str, target_fps: int = 15) -> AsyncGenerator[Any, None]:
    """Async generator that yields frames from RTSP stream at target_fps."""
    if not CV2_AVAILABLE:
        raise ImportError("opencv-python not installed")

    interval = 1.0 / target_fps
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open RTSP stream: {rtsp_url}")

    try:
        while True:
            t0 = time.monotonic()
            ok, frame = await asyncio.get_event_loop().run_in_executor(None, cap.read)
            if not ok:
                logger.warning("Frame read failed — retrying in 1s")
                await asyncio.sleep(1)
                continue
            yield frame
            elapsed = time.monotonic() - t0
            await asyncio.sleep(max(0, interval - elapsed))
    finally:
        cap.release()


async def run_pipeline(
    rtsp_url: str,
    hef_path: Path,
    mqtt_host: str,
    mqtt_port: int,
    room: str,
    target_fps: int = 15,
    confidence_threshold: float = 0.5,
) -> None:
    """Main pipeline loop. Runs until cancelled."""
    detector = HailoDetector(hef_path, confidence_threshold)
    detector.load()
    tracker = ObjectTracker()

    frame_count = 0
    fps_window_start = time.monotonic()

    try:
        async with aiomqtt.Client(mqtt_host, mqtt_port) as mqtt:
            async for frame in _capture_frames(rtsp_url, target_fps):
                t0 = time.monotonic()

                detections: list[Detection] = await asyncio.get_event_loop().run_in_executor(
                    None, detector.detect, frame
                )

                latency_ms = (time.monotonic() - t0) * 1000
                if INFERENCE_HIST:
                    INFERENCE_HIST.observe(latency_ms)

                frame_shape: tuple[int, int] = frame.shape[:2]
                tracks: list[Track] = tracker.update(detections, frame_shape)

                frame_count += 1
                elapsed = time.monotonic() - fps_window_start
                if elapsed >= 1.0:
                    current_fps = frame_count / elapsed
                    if FPS_GAUGE:
                        FPS_GAUGE.set(current_fps)
                    frame_count = 0
                    fps_window_start = time.monotonic()

                for track in tracks:
                    det = track.detection
                    if DETECTIONS_COUNTER:
                        DETECTIONS_COUNTER.labels(label=det.label).inc()
                    payload = {
                        "room": room,
                        "event_type": "detection",
                        "label": det.label,
                        "confidence": det.confidence,
                        "bbox": list(det.bbox),
                        "track_id": track.track_id,
                        "tier": 1,
                    }
                    await mqtt.publish(
                        f"home/{room}/camera/event",
                        json.dumps(payload),
                    )
    finally:
        detector.close()


if __name__ == "__main__":
    if PROM_AVAILABLE:
        from prometheus_client import start_http_server

        start_http_server(8001)
    asyncio.run(
        run_pipeline(
            rtsp_url=os.environ["RTSP_URL"],
            hef_path=Path(os.environ.get("HEF_PATH", "models/yolo11n_coco.hef")),
            mqtt_host=os.environ.get("MQTT_HOST", "mosquitto"),
            mqtt_port=int(os.environ.get("MQTT_PORT", "1883")),
            room=os.environ.get("ROOM", "living_room"),
            target_fps=int(os.environ.get("TARGET_FPS", "15")),
        )
    )
