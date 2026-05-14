"""Async CV cascade: RTSP -> detect -> track -> pose -> fall_rule -> MQTT.

Pipeline composition (T2.13):
    Stage 1  YOLO26n detect (Hailo, NMS-free)-> Detection[]
    Stage 2  ByteTrack tracker               -> Track[]
    Stage 3  YOLOv11n-pose on person tracks  -> Keypoints       [optional]
    Stage 4  FallDetector rule               -> FallEvent       [optional]
    Stage 5  MQTT publish:
                home/{room}/camera/event     (every detection)
                home/{room}/alert            (fall events)

A separate FusionEngine task subscribes to camera/event + sensors and emits
home/{room}/event/fused — see hub.edge.cv.fusion.

Atomic deploy support: SIGHUP triggers a model reload from the symlink target
without dropping the RTSP connection. The container is therefore reload-safe
and supports T4.5 acceptance ("cv container reload без downtime").

Target: 15 FPS sustained, <5% dropped frames.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from hub.edge.cv.detector import Detection, HailoDetector
from hub.edge.cv.fall_rule import FallDetector
from hub.edge.cv.fusion import FusionEngine
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

# Pose stage is optional — falls back to detect+track only if pose model
# isn't available (e.g., dev environment without Hailo SDK).
try:
    from hub.edge.cv.pose import Keypoints, PoseEstimator

    POSE_IMPORT_OK = True
except ImportError:
    POSE_IMPORT_OK = False
    PoseEstimator = None  # type: ignore[assignment,misc]
    Keypoints = None  # type: ignore[assignment,misc]


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
    FALL_COUNTER: Any = Counter(
        "iot_hub_cv_fall_alerts_total",
        "Total fall alerts published",
        ["confidence_bucket"],
    )
    RELOAD_COUNTER: Any = Counter(
        "iot_hub_cv_model_reloads_total",
        "Total successful model reloads (SIGHUP)",
    )
else:
    FPS_GAUGE = INFERENCE_HIST = DETECTIONS_COUNTER = FALL_COUNTER = RELOAD_COUNTER = None


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


class CVPipeline:
    """Stateful cascade pipeline with SIGHUP-triggered model reload.

    The detector and pose estimator are reloaded from their symlinked HEF
    paths on SIGHUP. This is the receiving end of T4.5 atomic deploy:
    `hub.edge.mlops.deploy.ModelStore.promote()` swaps the symlink, then
    sends SIGHUP to this process; on the next frame iteration the new
    model is loaded without losing the RTSP connection.
    """

    def __init__(
        self,
        rtsp_url: str,
        hef_path: Path,
        pose_hef_path: Path | None,
        mqtt_host: str,
        mqtt_port: int,
        room: str,
        target_fps: int = 15,
        confidence_threshold: float = 0.5,
    ) -> None:
        self.rtsp_url = rtsp_url
        self.hef_path = hef_path
        self.pose_hef_path = pose_hef_path
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.room = room
        self.target_fps = target_fps
        self.confidence_threshold = confidence_threshold

        self._detector: HailoDetector | None = None
        self._pose: PoseEstimator | None = None
        self._tracker = ObjectTracker()
        self._fall = FallDetector()
        self._reload_requested = False

    def _load_models(self) -> None:
        """Load (or reload) detector + pose models from their HEF paths.

        Symlinks are followed via Path.resolve() so atomic swap picks up the
        new target. Pose loading is best-effort — pipeline degrades to
        detect+track when pose isn't available.
        """
        if self._detector is not None:
            try:
                self._detector.close()
            except Exception:
                logger.exception("Detector close failed during reload")
        resolved = self.hef_path.resolve() if self.hef_path.is_symlink() else self.hef_path
        self._detector = HailoDetector(resolved, self.confidence_threshold)
        self._detector.load()
        logger.info("Loaded detector HEF: %s", resolved)

        if POSE_IMPORT_OK and self.pose_hef_path is not None and self.pose_hef_path.exists():
            try:
                if self._pose is not None:
                    self._pose.close()
                pose_resolved = (
                    self.pose_hef_path.resolve()
                    if self.pose_hef_path.is_symlink()
                    else self.pose_hef_path
                )
                assert PoseEstimator is not None
                self._pose = PoseEstimator(pose_resolved)
                self._pose.load()
                logger.info("Loaded pose HEF: %s", pose_resolved)
            except (ImportError, NotImplementedError, RuntimeError) as e:
                logger.warning("Pose stage disabled (%s); cascade will skip fall detection", e)
                self._pose = None
        else:
            self._pose = None

    def request_reload(self) -> None:
        """Set the reload flag — picked up at the start of the next frame."""
        logger.info("SIGHUP received — model reload queued for next frame")
        self._reload_requested = True

    def _publish_detection_payload(self, track: Track) -> dict[str, Any]:
        det = track.detection
        return {
            "room": self.room,
            "event_type": "detection",
            "label": det.label,
            "confidence": det.confidence,
            "bbox": list(det.bbox),
            "track_id": track.track_id,
            "tier": 1,
        }

    async def _maybe_run_fall(self, frame: Any, track: Track, mqtt: Any) -> None:
        """Run pose + fall_rule on a person track; publish alert if triggered."""
        if self._pose is None or track.detection.label != "person":
            return
        try:
            keypoints = self._pose.estimate(frame, track.detection.bbox)
        except (NotImplementedError, RuntimeError) as e:
            # Hailo pipeline not available at runtime — degrade silently.
            logger.debug("Pose estimate skipped: %s", e)
            return
        if keypoints is None:
            return
        fall = self._fall.update(track.track_id, keypoints, track.detection.bbox)
        if fall is None:
            return
        if FALL_COUNTER is not None:
            FALL_COUNTER.labels(confidence_bucket="high" if fall.confidence >= 1.0 else "low").inc()
        alert = {
            "room": self.room,
            "event_type": "fall",
            "track_id": fall.track_id,
            "confidence": fall.confidence,
            "bbox_ratio": round(fall.bbox_ratio, 3),
            "spine_angle_deg": round(fall.spine_angle_deg, 1),
            "tier": 1,
        }
        await mqtt.publish(f"home/{self.room}/alert", json.dumps(alert))
        logger.info("Fall alert published: track=%d conf=%.2f", fall.track_id, fall.confidence)

    async def run(self) -> None:
        """Main pipeline loop. Runs until cancelled."""
        self._load_models()

        frame_count = 0
        fps_window_start = time.monotonic()

        try:
            async with aiomqtt.Client(self.mqtt_host, self.mqtt_port) as mqtt:
                async for frame in _capture_frames(self.rtsp_url, self.target_fps):
                    if self._reload_requested:
                        try:
                            self._load_models()
                            if RELOAD_COUNTER is not None:
                                RELOAD_COUNTER.inc()
                            logger.info("Models reloaded successfully")
                        except Exception:
                            logger.exception("Model reload failed — continuing with previous")
                        self._reload_requested = False

                    assert self._detector is not None
                    t0 = time.monotonic()
                    detections: list[Detection] = await asyncio.get_event_loop().run_in_executor(
                        None, self._detector.detect, frame
                    )
                    latency_ms = (time.monotonic() - t0) * 1000
                    if INFERENCE_HIST is not None:
                        INFERENCE_HIST.observe(latency_ms)

                    frame_shape: tuple[int, int] = frame.shape[:2]
                    tracks: list[Track] = self._tracker.update(detections, frame_shape)

                    frame_count += 1
                    elapsed = time.monotonic() - fps_window_start
                    if elapsed >= 1.0:
                        if FPS_GAUGE is not None:
                            FPS_GAUGE.set(frame_count / elapsed)
                        frame_count = 0
                        fps_window_start = time.monotonic()

                    for track in tracks:
                        if DETECTIONS_COUNTER is not None:
                            DETECTIONS_COUNTER.labels(label=track.detection.label).inc()
                        await mqtt.publish(
                            f"home/{self.room}/camera/event",
                            json.dumps(self._publish_detection_payload(track)),
                        )
                        await self._maybe_run_fall(frame, track, mqtt)
        finally:
            if self._detector is not None:
                self._detector.close()
            if self._pose is not None:
                self._pose.close()


async def run_pipeline_with_fusion(
    rtsp_url: str,
    hef_path: Path,
    pose_hef_path: Path | None,
    mqtt_host: str,
    mqtt_port: int,
    room: str,
    target_fps: int = 15,
    confidence_threshold: float = 0.5,
    enable_fusion: bool = True,
) -> None:
    """Top-level entry: run the cascade + FusionEngine concurrently.

    SIGHUP is wired to CVPipeline.request_reload via the asyncio loop's
    signal handler.
    """
    pipeline = CVPipeline(
        rtsp_url=rtsp_url,
        hef_path=hef_path,
        pose_hef_path=pose_hef_path,
        mqtt_host=mqtt_host,
        mqtt_port=mqtt_port,
        room=room,
        target_fps=target_fps,
        confidence_threshold=confidence_threshold,
    )

    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGHUP, pipeline.request_reload)
    except (NotImplementedError, RuntimeError):
        # signal.SIGHUP not supported on this platform (e.g., Windows).
        logger.warning("SIGHUP handler not registered — atomic deploy reload disabled")

    tasks = [asyncio.create_task(pipeline.run(), name="cv-pipeline")]
    if enable_fusion:
        fusion = FusionEngine()
        tasks.append(asyncio.create_task(fusion.run(mqtt_host, mqtt_port), name="cv-fusion"))

    try:
        await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            t.cancel()


# Backwards-compatible single-loop entry (used in tests and for the
# detect-only Docker mode). Delegates to CVPipeline without fusion.
async def run_pipeline(
    rtsp_url: str,
    hef_path: Path,
    mqtt_host: str,
    mqtt_port: int,
    room: str,
    target_fps: int = 15,
    confidence_threshold: float = 0.5,
) -> None:
    """Detect+track only loop (no pose, no fusion). Kept for compatibility."""
    pipeline = CVPipeline(
        rtsp_url=rtsp_url,
        hef_path=hef_path,
        pose_hef_path=None,
        mqtt_host=mqtt_host,
        mqtt_port=mqtt_port,
        room=room,
        target_fps=target_fps,
        confidence_threshold=confidence_threshold,
    )
    await pipeline.run()


if __name__ == "__main__":
    if PROM_AVAILABLE:
        from prometheus_client import start_http_server

        start_http_server(int(os.environ.get("METRICS_PORT", "8002")))

    pose_path_env = os.environ.get("POSE_HEF_PATH")
    asyncio.run(
        run_pipeline_with_fusion(
            rtsp_url=os.environ["RTSP_URL"],
            hef_path=Path(os.environ.get("HEF_PATH", "/app/models/current_yolo.hef")),
            pose_hef_path=Path(pose_path_env) if pose_path_env else None,
            mqtt_host=os.environ.get("MQTT_HOST", "mosquitto"),
            mqtt_port=int(os.environ.get("MQTT_PORT", "1883")),
            room=os.environ.get("ROOM", "living_room"),
            target_fps=int(os.environ.get("TARGET_FPS", "15")),
            enable_fusion=os.environ.get("ENABLE_FUSION", "true").lower() == "true",
        )
    )
