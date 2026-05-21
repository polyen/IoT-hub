"""Hailo-8 inference wrapper for YOLO26n detection.

Requires HailoRT 4.17+ + hailo_platform installed on RPi5.
On other platforms, raises ImportError with a clear message.

YOLO26n / Hailo compilation note
--------------------------------
Hailo hardware does not support the NMS-free head's top-k ops (TopK,
GatherElements, ReduceMax). The HEF is compiled with the graph cut into TWO
separate output tensors, *before* those ops:

    /model.23/Mul_2    -> box   (1, 4, 8400)  xyxy in input pixels
    /model.23/Sigmoid  -> cls   (1, 3, 8400)  per-class score 0-1

The cut must keep box and class as SEPARATE outputs. An earlier build cut at
the concatenated /model.23/Concat_3 (1, 7, 8400): box (~0-640) and class (0-1)
then shared one uint8 quantisation scale, so every class score collapsed into
the zero bucket and the detector never fired. Keep the two-output cut — see
``training/convert_to_hef.py --end-nodes``.

HailoRT API note: uses VDevice.create_infer_model (4.17+ API). The older
InferVStreams/InputVStreamParams flow was removed and will raise AttributeError
on HailoRT 4.23+.

Model classes (mixed dataset built by ``training.datasets.prepare_mixed``):
0=person, 1=fire, 2=smoke. The class order is locked by the data.yaml — keep
it in sync when retraining.
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


DETECTION_CLASSES: dict[int, str] = {0: "person", 1: "fire", 2: "smoke"}

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
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2 normalized 0-1


class HailoDetector:
    """Batch=1 YOLO26n inference on Hailo-8.

    YOLO26n HEFs are compiled with the NMS-free head cut into two separate
    output tensors — box (4 channels) and class (nc channels) — see the module
    docstring. detect() reads both, applies the confidence threshold and a
    CPU-side greedy NMS, and returns Detection objects with 0-1 normalised
    boxes.

    Uses HailoRT 4.17+ create_infer_model API. The ConfiguredInferModel context
    is kept alive via ExitStack for zero-overhead per-frame inference.
    """

    def __init__(
        self,
        hef_path: Path,
        confidence_threshold: float = 0.5,
        nms_free: bool = False,
    ) -> None:
        if not HAILO_AVAILABLE:
            raise ImportError(
                "hailo_platform not installed — run on RPi5 with HailoRT. "
                "See scripts/edge-bootstrap.sh"
            )
        self._hef_path = hef_path
        self._confidence_threshold = confidence_threshold
        # YOLO26's one2one head deduplicates predictions on-device; with
        # nms_free=True the CPU-side NMS in _postprocess is skipped.
        self._nms_free = nms_free

        self._device: Any = None
        self._infer_model: Any = None
        self._configured: Any = None
        self._exit_stack: Any = None
        self._input_h: int = 640
        self._input_w: int = 640

        # Split-head outputs, resolved in load().
        self._box_name: str | None = None
        self._cls_name: str | None = None
        self._box_buf: Any = None
        self._cls_buf: Any = None

    def load(self, device: Any = None, scheduled: bool = False) -> None:
        """Load HEF and open the Hailo inference pipeline.

        The HEF carries two outputs (box + class, see module docstring). Both
        are requested as FLOAT32 so HailoRT dequantises each with its own
        scale; a buffer per output is pre-allocated and reused across frames.

        Pass an already-open VDevice as `device` to share it with other models
        (the Hailo-8 allows only one VDevice owner per process). When `device`
        is None a new VDevice is created and owned by this instance.
        Set `scheduled=True` when the VDevice uses ROUND_ROBIN scheduler —
        manual .activate() is forbidden in that mode; the scheduler handles it.
        """
        import numpy as np

        self._owns_device = device is None
        self._device = VDevice() if device is None else device
        self._infer_model = self._device.create_infer_model(str(self._hef_path))
        self._infer_model.set_batch_size(1)

        # Auto-quantize input (float32 → uint8) and auto-dequantize outputs.
        self._infer_model.input().set_format_type(FormatType.FLOAT32)
        input_info = self._infer_model.input()
        shape = input_info.shape  # (H, W, C) — no batch dimension
        self._input_h = int(shape[0])
        self._input_w = int(shape[1])

        outputs = list(self._infer_model.outputs)
        if len(outputs) != 2:
            raise RuntimeError(
                f"HEF {self._hef_path.name} has {len(outputs)} output(s); expected 2 "
                "(box + class). Recompile cutting the graph at the separate end-nodes "
                "/model.23/Mul_2 and /model.23/Sigmoid — see the module docstring."
            )

        nc = len(DETECTION_CLASSES)
        for o in outputs:
            o.set_format_type(FormatType.FLOAT32)
            out_shape = tuple(int(d) for d in o.shape)
            if 4 in out_shape and nc not in out_shape:
                self._box_name = o.name
                self._box_buf = np.empty(out_shape, dtype=np.float32)
            elif nc in out_shape:
                self._cls_name = o.name
                self._cls_buf = np.empty(out_shape, dtype=np.float32)
        if self._box_name is None or self._cls_name is None:
            raise RuntimeError(
                "Could not map HEF outputs to box/class by shape "
                f"({[tuple(int(d) for d in o.shape) for o in outputs]}); "
                f"expected one with 4 channels and one with {nc}."
            )

        self._exit_stack = contextlib.ExitStack()
        self._configured = self._exit_stack.enter_context(self._infer_model.configure())
        # HailoRT 4.23: explicit activate() required in non-scheduled mode only.
        # With ROUND_ROBIN scheduler, activate() is forbidden — the scheduler
        # manages network activation automatically.
        if not scheduled:
            activate_result = self._configured.activate()
            if hasattr(activate_result, "__enter__"):
                self._exit_stack.enter_context(activate_result)

        logger.info(
            "Hailo detector loaded: %s (input %dx%d, box=%s cls=%s, classes=%s)",
            self._hef_path.name,
            self._input_w,
            self._input_h,
            self._box_buf.shape,
            self._cls_buf.shape,
            list(DETECTION_CLASSES.values()),
        )

    def detect(self, frame_bgr: Any) -> list[Detection]:
        """Run inference on a BGR frame. Returns detections with confidence >= threshold."""
        import cv2
        import numpy as np

        if self._configured is None:
            raise RuntimeError("Call load() before detect()")
        assert self._box_name is not None and self._cls_name is not None

        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(frame_rgb, (self._input_w, self._input_h))
        # The HEF input is calibrated on [0,1] images (training.convert_to_hef
        # divides pixels by 255) — feed the same range, not raw [0,255].
        input_tensor = np.ascontiguousarray(resized.astype(np.float32) / 255.0)

        bindings = self._configured.create_bindings()
        bindings.input().set_buffer(input_tensor)
        bindings.output(self._box_name).set_buffer(self._box_buf)
        bindings.output(self._cls_name).set_buffer(self._cls_buf)
        self._configured.run([bindings], timeout=1000)

        return self._postprocess(self._box_buf, self._cls_buf)

    def _postprocess(self, box_raw: Any, cls_raw: Any) -> list[Detection]:
        """Convert split box/class output tensors → Detection list with CPU NMS.

        ``box_raw`` is xyxy in input-pixel space; ``cls_raw`` holds per-class
        scores already passed through sigmoid. Boxes are normalised to 0-1 —
        the unit ObjectTracker and the rest of the cascade expect.
        """
        import numpy as np

        nc = len(DETECTION_CLASSES)
        box = np.squeeze(np.asarray(box_raw))  # (4, A) or (A, 4)
        cls = np.squeeze(np.asarray(cls_raw))  # (nc, A) or (A, nc)
        if box.shape[0] == 4:
            box = box.T
        if cls.shape[0] == nc:
            cls = cls.T
        # box: (A, 4), cls: (A, nc)

        class_ids = np.argmax(cls, axis=1)
        confidences = cls[np.arange(len(class_ids)), class_ids]

        mask = confidences >= self._confidence_threshold
        if not mask.any():
            return []

        box = box[mask].astype(np.float32)
        class_ids = class_ids[mask]
        confidences = confidences[mask]

        # xyxy in input-pixel space → 0-1 normalised.
        box[:, 0::2] /= self._input_w
        box[:, 1::2] /= self._input_h

        keep = list(range(len(confidences))) if self._nms_free else self._nms(box, confidences)
        return [
            Detection(
                class_id=int(class_ids[i]),
                label=DETECTION_CLASSES.get(int(class_ids[i]), "unknown"),
                confidence=float(confidences[i]),
                bbox=(
                    float(box[i, 0]),
                    float(box[i, 1]),
                    float(box[i, 2]),
                    float(box[i, 3]),
                ),
            )
            for i in keep
        ]

    def _nms(self, boxes: Any, scores: Any, iou_threshold: float = 0.45) -> list[int]:
        """Greedy IoU-based NMS. Returns indices of kept boxes."""
        import numpy as np

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
        if self._device is not None and getattr(self, "_owns_device", True):
            self._device.release()
        self._device = None


if __name__ == "__main__":
    import cv2

    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--hef", default="models/versions/fire_smoke-v1.3.hef")
    parser.add_argument("--conf", type=float, default=0.5)
    args = parser.parse_args()
    detector = HailoDetector(Path(args.hef), confidence_threshold=args.conf)
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
