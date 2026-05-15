"""Hailo-8 inference wrapper for YOLO detection (YOLO26n primary, YOLO11n legacy).

Requires HailoRT + hailo_platform installed on RPi5.
On other platforms, raises ImportError with a clear message.

YOLO26n compilation note: Hailo hardware does not support NMS operations
(GatherElements, TopK, ReduceMax). The HEF is therefore compiled with the graph
cut at /model.23/Transpose (before the NMS decode head). The detector's
inference loop applies CPU-side NMS after NPU inference.

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
    from hailo_platform import (  # noqa: F401  # noqa: F401
        HEF,
        InferVStreams,
        InputVStreamParams,
        OutputVStreamParams,
        VDevice,
    )

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

    The InferVStreams pipeline is opened once in load() and kept alive until
    close() — avoid per-frame context-manager overhead.
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
        self._network_group: Any = None
        self._infer_pipeline: Any = None
        self._exit_stack: Any = None
        self._input_name: str = ""
        self._output_name: str = ""
        self._input_h: int = 640
        self._input_w: int = 640

    def load(self) -> None:
        """Load HEF, open Hailo device, and activate the inference pipeline.

        Uses HailoRT 4.17+ API: VDevice.configure(hef) without the removed
        create_configure_params(). The network group and InferVStreams are kept
        open via an ExitStack for zero-overhead per-frame inference.
        """
        hef = HEF(str(self._hef_path))
        self._device = VDevice()

        network_groups = self._device.configure(hef)
        self._network_group = network_groups[0]
        network_group_params = self._network_group.create_params()

        input_infos = hef.get_input_vstream_infos()
        output_infos = hef.get_output_vstream_infos()
        self._input_name = input_infos[0].name
        self._output_name = output_infos[0].name
        shape = input_infos[0].shape  # (H, W, C)
        self._input_h = int(shape[0])
        self._input_w = int(shape[1])

        self._exit_stack = contextlib.ExitStack()
        activated = self._exit_stack.enter_context(
            self._network_group.activate(network_group_params)
        )
        input_params = InputVStreamParams.make_from_network_group(activated, quantized=False)
        output_params = OutputVStreamParams.make_from_network_group(activated, quantized=False)
        self._infer_pipeline = self._exit_stack.enter_context(
            InferVStreams(activated, input_params, output_params)
        )
        logger.info(
            "Hailo detector loaded: %s (input %dx%d, classes=%s)",
            self._hef_path.name,
            self._input_w,
            self._input_h,
            list(FIRE_SMOKE_CLASSES.values()),
        )

    def detect(self, frame_bgr: Any) -> list[Detection]:
        """Run inference on a BGR frame. Returns detections with confidence >= threshold."""
        import cv2  # type: ignore[import]
        import numpy as np  # type: ignore[import]

        if self._infer_pipeline is None:
            raise RuntimeError("Call load() before detect()")

        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(frame_rgb, (self._input_w, self._input_h))
        # HailoRT expects float32 in [0, 1] when quantized=False
        input_tensor = np.expand_dims(resized.astype(np.float32) / 255.0, axis=0)

        results = self._infer_pipeline.infer({self._input_name: input_tensor})
        raw = results[self._output_name][0]  # drop batch dim

        return self._postprocess(raw)

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
