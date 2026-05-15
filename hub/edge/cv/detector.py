"""Hailo-8 inference wrapper for YOLO detection (YOLO26n primary, YOLO11n legacy).

Requires HailoRT + hailo_platform installed on RPi5.
On other platforms, raises ImportError with a clear message.

YOLO26n compilation note: Hailo hardware does not support NMS operations
(GatherElements, TopK, ReduceMax). The HEF is therefore compiled with the graph
cut at /model.23/Transpose (before the NMS decode head). The detector's
inference loop must apply CPU-side NMS after NPU inference — pass
nms_on_cpu=True (default) to enable this. See hailo-rpi5-examples for the
stream API reference.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from hailo_platform import HEF, VDevice  # noqa: F401

    HAILO_AVAILABLE = True
except ImportError:
    HAILO_AVAILABLE = False


@dataclass
class Detection:
    class_id: int
    label: str
    confidence: float
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2 normalized


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


class HailoDetector:
    """Batch=1 YOLO inference on Hailo-8 (YOLO26n primary; YOLO11n still loadable).

    YOLO26n HEFs are compiled with the graph cut before the NMS head (Hailo
    hardware limitation). Set nms_on_cpu=True (default) so detect() applies
    CPU-side NMS on the raw /model.23/Transpose output tensors.
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

    def load(self) -> None:
        """Load HEF and initialize Hailo device. Call once before detect()."""
        hef = HEF(str(self._hef_path))
        self._device = VDevice()
        configure_params = self._device.create_configure_params(hef)
        network_groups = self._device.configure(hef, configure_params)
        self._network_group = network_groups[0]
        logger.info("Hailo detector loaded: %s", self._hef_path.name)

    def detect(self, frame_bgr: Any) -> list[Detection]:
        """Run inference on a BGR frame (numpy array 640x640 or any size).

        Returns list of Detection with confidence >= threshold.
        """
        if self._network_group is None:
            raise RuntimeError("Call load() before detect()")
        # Preprocessing + inference omitted — depends on hailo_platform stream API
        # See https://github.com/hailo-ai/hailo-rpi5-examples for reference implementation
        raise NotImplementedError(
            "Hailo inference loop not yet implemented — see hailo-rpi5-examples"
        )

    def close(self) -> None:
        if self._device is not None:
            self._device.release()
            self._device = None


if __name__ == "__main__":
    import cv2

    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--hef", default="models/versions/yolo26n_coco.hef")
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
