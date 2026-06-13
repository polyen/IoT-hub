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
        class_thresholds: dict[int, float] | None = None,
    ) -> None:
        if not HAILO_AVAILABLE:
            raise ImportError(
                "hailo_platform not installed — run on RPi5 with HailoRT. "
                "See scripts/edge-bootstrap.sh"
            )
        self._hef_path = hef_path
        self._confidence_threshold = confidence_threshold
        # Per-class confidence floors. ``person`` (class 0) is kept lower than
        # fire/smoke: at surveillance distance the quantised YOLO26n score
        # hovers right at 0.5, so a single 0.5 floor makes the person bbox
        # flicker frame-to-frame (drops below 0.5 → track lost → new track_id →
        # duplicate identity events). A lower person floor keeps the detection
        # continuous so the track — and the identity — stays stable. fire/smoke
        # stay at the global floor to avoid extra false alarms. Classes absent
        # from this map fall back to ``confidence_threshold``.
        self._class_thresholds: dict[int, float] = class_thresholds or {}
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
        # DFL path (NMS-head HEFs cut at /model.23/Concat, box = 4*reg_max
        # channels of raw distribution bins instead of decoded xyxy). Resolved
        # in load(); anchors/strides built once when self._box_dfl is True.
        self._box_dfl: bool = False
        self._anchors: Any = None
        self._strides: Any = None

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
        # Box output is either decoded xyxy (4 channels, YOLO26 NMS-free, cut at
        # /model.23/Mul_2) or raw DFL bins (4*reg_max e.g. 64, NMS-head models
        # like YOLOv8/v11 cut at /model.23/Concat). Class always has nc channels.
        for o in outputs:
            o.set_format_type(FormatType.FLOAT32)
            out_shape = tuple(int(d) for d in o.shape)
            if nc in out_shape and 4 not in out_shape and 64 not in out_shape:
                self._cls_name = o.name
                self._cls_buf = np.empty(out_shape, dtype=np.float32)
            elif 4 in out_shape or 64 in out_shape:
                self._box_name = o.name
                self._box_buf = np.empty(out_shape, dtype=np.float32)
                self._box_dfl = 64 in out_shape
        if self._box_name is None or self._cls_name is None:
            raise RuntimeError(
                "Could not map HEF outputs to box/class by shape "
                f"({[tuple(int(d) for d in o.shape) for o in outputs]}); "
                f"expected class with {nc} channels and box with 4 (decoded) "
                "or 4*reg_max (DFL) channels."
            )
        if self._box_dfl:
            self._build_dfl_anchors()

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
        box = np.squeeze(np.asarray(box_raw))  # (4|64, A) or (A, 4|64)
        cls = np.squeeze(np.asarray(cls_raw))  # (nc, A) or (A, nc)
        if self._box_dfl:
            box = self._dfl_decode(box)  # -> (A, 4) xyxy in input pixels
        elif box.shape[0] == 4:
            box = box.T
        if cls.shape[0] == nc:
            cls = cls.T
        # box: (A, 4), cls: (A, nc)

        class_ids = np.argmax(cls, axis=1)
        confidences = cls[np.arange(len(class_ids)), class_ids]

        # Per-detection threshold: a per-class floor when set, else the global
        # one. Vectorised so the mask stays a single numpy comparison.
        if self._class_thresholds:
            thresholds = np.array(
                [self._class_thresholds.get(int(c), self._confidence_threshold) for c in class_ids],
                dtype=confidences.dtype,
            )
            mask = confidences >= thresholds
        else:
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

    def _build_dfl_anchors(self) -> None:
        """Pre-compute anchor points + strides for the DFL box decode (once).

        Standard 3-level FPN (strides 8/16/32). Anchor points are grid-cell
        centres in feature-map coordinates; concatenated P3→P4→P5 to match the
        ONNX/HEF flattening order (e.g. 6400+1600+400 = 8400 for 640 input).
        """
        import numpy as np

        pts: list[Any] = []
        strs: list[Any] = []
        for stride in (8, 16, 32):
            h = self._input_h // stride
            w = self._input_w // stride
            sx = np.arange(w, dtype=np.float32) + 0.5
            sy = np.arange(h, dtype=np.float32) + 0.5
            gy, gx = np.meshgrid(sy, sx, indexing="ij")
            pts.append(np.stack((gx.ravel(), gy.ravel()), axis=1))  # (h*w, 2)
            strs.append(np.full((h * w, 1), float(stride), dtype=np.float32))
        self._anchors = np.concatenate(pts, axis=0)  # (A, 2)
        self._strides = np.concatenate(strs, axis=0)  # (A, 1)

    def _dfl_decode(self, box: Any) -> Any:
        """Decode raw DFL distribution bins → xyxy in input-pixel space.

        ``box`` is (4*reg_max, A) or (A, 4*reg_max). Each side's reg_max bins
        are soft-maxed and reduced to an expected distance, then the (l,t,r,b)
        distances are applied to the anchor points and scaled by stride —
        reproducing the Ultralytics DFL + dist2bbox the NMS-free head folds in.
        """
        import numpy as np

        if box.shape[0] != self._anchors.shape[0]:
            box = box.T  # -> (A, 4*reg_max)
        a = box.shape[0]
        reg = box.shape[1] // 4
        d = box.reshape(a, 4, reg).astype(np.float32)
        d = d - d.max(axis=2, keepdims=True)  # stable softmax
        e = np.exp(d)
        p = e / e.sum(axis=2, keepdims=True)
        dist = (p * np.arange(reg, dtype=np.float32)).sum(axis=2)  # (A, 4) l,t,r,b
        x1y1 = self._anchors - dist[:, :2]
        x2y2 = self._anchors + dist[:, 2:]
        return (np.concatenate([x1y1, x2y2], axis=1) * self._strides).astype(np.float32)

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
