"""Hailo-8 inference wrapper for YOLO detection (YOLO26n primary, YOLO11n legacy).

Requires HailoRT 4.17+ + hailo_platform installed on RPi5.
On other platforms, raises ImportError with a clear message.

YOLO26n compilation note: Hailo hardware does not support NMS operations
(GatherElements, TopK, ReduceMax). The HEF is therefore compiled with the graph
cut at /model.23/Transpose (before the NMS decode head). The detector's
inference loop applies CPU-side NMS after NPU inference.

HailoRT API note: uses VDevice.create_infer_model (4.17+ API). The older
InferVStreams/InputVStreamParams flow was removed and will raise AttributeError
on HailoRT 4.23+.

Model classes (fire/smoke dataset): 0=fire, 1=smoke.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from hailo_platform import FormatType, VDevice  # noqa: F401

    HAILO_AVAILABLE = True
except ImportError:
    HAILO_AVAILABLE = False


FIRE_SMOKE_CLASSES: dict[int, str] = {0: "fire", 1: "smoke"}

# Kept for backward-compatibility with scripts that import COCO_CLASSES.
COCO_CLASSES = [
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
]


@dataclass
class Detection:
    class_id: int
    label: str
    confidence: float
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2 normalized


class HailoDetector:
    """Batch=1 YOLO inference on Hailo-8 (YOLO26n primary; YOLO11n still loadable).

    YOLO26n HEFs are compiled with the graph cut before the NMS head (Hailo
    hardware limitation). CPU-side NMS is applied in detect() on the raw
    /model.23/Transpose output tensors.

    Uses HailoRT 4.17+ create_infer_model API. The ConfiguredInferModel context
    is kept alive via ExitStack for zero-overhead per-frame inference.
    """

    def __init__(
        self,
        hef_path: Path,
        confidence_threshold: float = 0.5,
        nms_on_cpu: bool = True,
    ) -> None:
        if not HAILO_AVAILABLE:
            raise ImportError(
                "hailo_platform not installed — run on RPi5 with HailoRT. "
                "See scripts/edge-bootstrap.sh"
            )
        self._hef_path = hef_path
        self._confidence_threshold = confidence_threshold
        self._nms_on_cpu = nms_on_cpu

        self._device: Any = None
        self._infer_model: Any = None
        self._configured: Any = None
        self._exit_stack: Any = None
        self._output_buf: Any = None
        self._input_h: int = 640
        self._input_w: int = 640

    def load(self) -> None:
        """Load HEF and open the Hailo inference pipeline.

        Uses HailoRT 4.17+ create_infer_model API (replaces the removed
        InferVStreams/InputVStreamParams flow). The ConfiguredInferModel is kept
        open via ExitStack; a single output buffer is pre-allocated and reused
        across frames to avoid per-inference allocation.
        """
        import numpy as np  # type: ignore[import]

        self._device = VDevice()
        self._infer_model = self._device.create_infer_model(str(self._hef_path))
        self._infer_model.set_batch_size(1)

        # Ask HailoRT to auto-quantize input (float32→uint8) and auto-dequantize
        # output (uint8→float32). The HEF carries the quant params.
        self._infer_model.input().set_format_type(FormatType.FLOAT32)
        self._infer_model.output().set_format_type(FormatType.FLOAT32)

        input_info = self._infer_model.input()
        shape = input_info.shape  # (H, W, C) — no batch dimension
        self._input_h = int(shape[0])
        self._input_w = int(shape[1])

        output_info = self._infer_model.output()
        output_shape = tuple(int(d) for d in output_info.shape)

        self._exit_stack = contextlib.ExitStack()
        self._configured = self._exit_stack.enter_context(self._infer_model.configure())
        self._output_buf = np.empty(output_shape, dtype=np.float32)

        logger.info(
            "Hailo detector loaded: %s (input %dx%d, output shape=%s, classes=%s)",
            self._hef_path.name,
            self._input_w,
            self._input_h,
            output_shape,
            list(FIRE_SMOKE_CLASSES.values()),
        )

    def detect(self, frame_bgr: Any) -> list[Detection]:
        """Run inference on a BGR frame. Returns detections with confidence >= threshold."""
        import cv2  # type: ignore[import]
        import numpy as np  # type: ignore[import]

        if self._configured is None:
            raise RuntimeError("Call load() before detect()")

        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(frame_rgb, (self._input_w, self._input_h))
        # FormatType.FLOAT32: HailoRT quantizes [0,255] float32 → uint8 before NPU.
        input_tensor = np.ascontiguousarray(resized.astype(np.float32))  # (H, W, C) 0–255

        bindings = self._configured.create_bindings()
        bindings.input().set_buffer(input_tensor)
        bindings.output().set_buffer(self._output_buf)
        self._configured.run([bindings], timeout=1000)

        return self._postprocess(self._output_buf)

    def _postprocess(self, raw: Any) -> list[Detection]:
        """Convert raw YOLO26n output → Detection list with CPU NMS."""
        import numpy as np  # type: ignore[import]

        nc = len(FIRE_SMOKE_CLASSES)
        # Output may be [4+nc, num_anchors] or [num_anchors, 4+nc]
        if raw.ndim == 2 and raw.shape[0] == 4 + nc:
            raw = raw.T  # → [num_anchors, 4+nc]

        boxes_cxcywh = raw[:, :4]
        class_scores = raw[:, 4:]

        class_ids = np.argmax(class_scores, axis=1)
        confidences = class_scores[np.arange(len(class_ids)), class_ids]

        mask = confidences >= self._confidence_threshold
        if not mask.any():
            return []

        boxes_cxcywh = boxes_cxcywh[mask]
        class_ids = class_ids[mask]
        confidences = confidences[mask]

        # cx, cy, w, h → x1, y1, x2, y2
        x1 = boxes_cxcywh[:, 0] - boxes_cxcywh[:, 2] / 2
        y1 = boxes_cxcywh[:, 1] - boxes_cxcywh[:, 3] / 2
        x2 = boxes_cxcywh[:, 0] + boxes_cxcywh[:, 2] / 2
        y2 = boxes_cxcywh[:, 1] + boxes_cxcywh[:, 3] / 2
        boxes_xyxy = np.stack([x1, y1, x2, y2], axis=1)

        keep = self._nms(boxes_xyxy, confidences)
        return [
            Detection(
                class_id=int(class_ids[i]),
                label=FIRE_SMOKE_CLASSES.get(int(class_ids[i]), "unknown"),
                confidence=float(confidences[i]),
                bbox=(float(x1[i]), float(y1[i]), float(x2[i]), float(y2[i])),
            )
            for i in keep
        ]

    def _nms(self, boxes: Any, scores: Any, iou_threshold: float = 0.45) -> list[int]:
        """Greedy IoU-based NMS. Returns indices of kept boxes."""
        import numpy as np  # type: ignore[import]

        order = scores.argsort()[::-1]
        keep: list[int] = []
        while len(order) > 0:
            i = int(order[0])
            keep.append(i)
            if len(order) == 1:
                break
            rest = order[1:]
            xx1 = np.maximum(boxes[i, 0], boxes[rest, 0])
            yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
            xx2 = np.minimum(boxes[i, 2], boxes[rest, 2])
            yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
            inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
            area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
            area_rest = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
            iou = inter / (area_i + area_rest - inter + 1e-6)
            order = rest[iou < iou_threshold]
        return keep

    def close(self) -> None:
        if self._exit_stack is not None:
            self._exit_stack.close()
            self._exit_stack = None
        self._configured = None
        self._infer_model = None
        if self._device is not None:
            self._device.release()
            self._device = None


if __name__ == "__main__":
    import cv2  # type: ignore[import]

    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--hef", default="models/versions/fire_smoke-v1.0.hef")
    args = parser.parse_args()
    detector = HailoDetector(Path(args.hef))
    detector.load()
    frame = cv2.imread(args.image)
    detections = detector.detect(frame)
    print(
        json.dumps(
            [
                {"label": d.label, "confidence": d.confidence, "bbox": list(d.bbox)}
                for d in detections
            ],
            indent=2,
        )
    )
    detector.close()
