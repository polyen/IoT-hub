"""Pose estimation wrapper — runs a YOLO-pose HEF on person crops via Hailo NPU.

Stage 3 of the CV cascade. Invoked once per tracked person per frame; the
caller (``pipeline.CVPipeline``) already throttles by track state.

Production HEF (2026-05): ``yolov8n_relu6_coco_pose`` from Hailo Model Zoo —
production-validated, single-class person pose, ~5 ms on Hailo-8 for a 640×640
crop. YOLO26n-pose is a planned upgrade once Hailo Model Zoo publishes a
production-ready HEF (community-tier compile is in progress as of May 2026 but
relies on a split HEF + ONNX postprocess flow that adds CPU latency).

Output convention: keypoints are returned in **frame-normalized** ``[0, 1]``
coordinates (same convention as ``Detection.bbox``) so downstream consumers
(``fall_rule.py``, ``face.crop_face_from_keypoints``) can use them without
knowing about the intermediate crop.

YOLO-pose HEF layout (compatible with both v8 and v26): output is
``[num_anchors, 4 + 1 + 17 * 3]`` (cxcywh + conf + 17 × (x, y, v)). The parser
also tolerates the transposed ``[4 + 1 + 17 * 3, num_anchors]`` layout — both
v8 and v26 HEFs are picked up automatically without code changes (the
``current_pose.hef`` symlink decides which weights are loaded at runtime).
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from hailo_platform import FormatType, VDevice  # type: ignore[import]

    HAILO_AVAILABLE = True
except ImportError:
    HAILO_AVAILABLE = False


NUM_KEYPOINTS = 17
_POSE_VECTOR_LEN = 4 + 1 + NUM_KEYPOINTS * 3  # 56


@dataclass
class Keypoints:
    """17 COCO keypoints as normalized ``(x, y, confidence)`` tuples."""

    points: list[tuple[float, float, float]]
    track_id: int = -1


class PoseEstimator:
    """Runs a YOLO-pose HEF (yolov8n-pose / yolo26n-pose) on person crops via Hailo NPU.

    Mirrors ``HailoDetector`` lifecycle: ``load()`` opens an inference context
    that is kept alive via ``ExitStack``; ``estimate()`` is called once per
    person per frame.
    """

    def __init__(
        self,
        hef_path: Path,
        confidence_threshold: float = 0.3,
    ) -> None:
        if not HAILO_AVAILABLE:
            raise ImportError("hailo_platform required — run on RPi5 with HailoRT")
        self._hef_path = hef_path
        self._confidence_threshold = confidence_threshold

        self._device: Any = None
        self._infer_model: Any = None
        self._configured: Any = None
        self._exit_stack: Any = None
        self._output_buf: Any = None
        self._input_h: int = 640
        self._input_w: int = 640

    def load(self) -> None:
        """Open HEF + allocate persistent input/output buffers."""
        import numpy as np  # type: ignore[import]

        self._device = VDevice()
        self._infer_model = self._device.create_infer_model(str(self._hef_path))
        self._infer_model.set_batch_size(1)
        self._infer_model.input().set_format_type(FormatType.FLOAT32)
        self._infer_model.output().set_format_type(FormatType.FLOAT32)

        input_info = self._infer_model.input()
        shape = input_info.shape  # (H, W, C)
        self._input_h = int(shape[0])
        self._input_w = int(shape[1])

        output_info = self._infer_model.output()
        output_shape = tuple(int(d) for d in output_info.shape)

        self._exit_stack = contextlib.ExitStack()
        self._configured = self._exit_stack.enter_context(self._infer_model.configure())
        activate_result = self._configured.activate()
        if hasattr(activate_result, "__enter__"):
            self._exit_stack.enter_context(activate_result)
        self._output_buf = np.empty(output_shape, dtype=np.float32)

        logger.info(
            "Pose HEF loaded: %s (input %dx%d, output shape=%s)",
            self._hef_path.name,
            self._input_w,
            self._input_h,
            output_shape,
        )

    def estimate(self, frame: Any, bbox: tuple[float, float, float, float]) -> Keypoints | None:
        """Extract 17 COCO keypoints for a single person crop.

        ``frame`` is the full BGR frame (``ndarray``); ``bbox`` is normalized
        ``(x1, y1, x2, y2)`` from the detector. Returns ``None`` when the crop
        is empty or no detection clears the confidence threshold.
        """
        import cv2  # type: ignore[import]
        import numpy as np  # type: ignore[import]

        if self._configured is None:
            raise RuntimeError("Call load() before estimate()")

        h, w = frame.shape[:2]
        x1, y1, x2, y2 = bbox
        px1 = max(0, int(x1 * w))
        py1 = max(0, int(y1 * h))
        px2 = min(w, int(x2 * w))
        py2 = min(h, int(y2 * h))
        if px2 <= px1 or py2 <= py1:
            return None
        crop_w = float(px2 - px1)
        crop_h = float(py2 - py1)

        crop_bgr = frame[py1:py2, px1:px2]
        crop_rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(crop_rgb, (self._input_w, self._input_h))
        input_tensor = np.ascontiguousarray(resized.astype(np.float32))

        bindings = self._configured.create_bindings()
        bindings.input().set_buffer(input_tensor)
        bindings.output().set_buffer(self._output_buf)
        self._configured.run([bindings], timeout=1000)

        raw = self._output_buf
        # Output may be [num_anchors, 56] or [56, num_anchors].
        if raw.ndim == 2 and raw.shape[0] == _POSE_VECTOR_LEN:
            raw = raw.T
        if raw.ndim != 2 or raw.shape[1] != _POSE_VECTOR_LEN:
            logger.debug("Unexpected pose output shape: %s", raw.shape)
            return None

        confs = raw[:, 4]
        best_idx = int(np.argmax(confs))
        if confs[best_idx] < self._confidence_threshold:
            return None

        # Each kp: (x, y, v) in model-input pixel coords [0, input_w/h].
        kps = raw[best_idx, 5:].reshape(NUM_KEYPOINTS, 3)
        points: list[tuple[float, float, float]] = []
        for kp_x, kp_y, kp_v in kps:
            # Model-input coords → crop-relative [0, 1] → frame-normalized [0, 1].
            nx_crop = float(kp_x) / self._input_w
            ny_crop = float(kp_y) / self._input_h
            frame_x = (px1 + nx_crop * crop_w) / w
            frame_y = (py1 + ny_crop * crop_h) / h
            points.append((frame_x, frame_y, float(kp_v)))

        return Keypoints(points=points)

    def close(self) -> None:
        if self._exit_stack is not None:
            self._exit_stack.close()
            self._exit_stack = None
        self._configured = None
        self._infer_model = None
        if self._device is not None:
            self._device.release()
            self._device = None
