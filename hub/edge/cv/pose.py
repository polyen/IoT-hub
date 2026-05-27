"""Pose estimation wrapper — runs a YOLO-pose HEF on person crops via Hailo NPU.

Stage 3 of the CV cascade. Invoked once per tracked person per frame; the
caller (``pipeline.CVPipeline``) already throttles by track state.

Production HEF (2026-05): ``yolov8s_pose`` from Hailo Model Zoo v2.14.0
(hailo8, 640×640, downloaded via hailo-rpi5-examples/download_resources.sh).

Supported HEF layouts
---------------------
Multi-output — ``yolov8s_pose`` / ``yolov8m_pose`` (9 tensors, 3 scales):
    [H, W, 64]  DFL bbox regression — not used; we only need keypoints.
    [H, W,  1]  objectness/confidence — sigmoid already applied by Hailo.
    [H, W, 51]  17 COCO keypoints × (x, y, visibility).
                x, y are logit(coord/input_size); sigmoid gives crop-normalised [0,1].
                visibility is a raw logit; ``_decode_multi`` applies sigmoid.
  Input must be normalised to [0, 1] (Hailo Model Zoo calibration convention).

Single-output (legacy, shape [num_anchors, 56] or [56, num_anchors]):
    Kept for forward-compat in case a single-output HEF is loaded.
    Input is sent as [0, 255] float32 (original calibration assumption).

Output convention
-----------------
Keypoints are returned in **frame-normalised** [0, 1] coordinates so
downstream consumers (``fall_rule.py``, ``face.crop_face_from_keypoints``)
can use them without knowing the intermediate crop geometry.
"""

from __future__ import annotations

import contextlib
import logging
import math
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
_POSE_VECTOR_LEN = 4 + 1 + NUM_KEYPOINTS * 3  # 56 — single-output legacy layout


@dataclass
class Keypoints:
    """17 COCO keypoints as normalised ``(x, y, confidence)`` tuples."""

    points: list[tuple[float, float, float]]
    track_id: int = -1


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-500.0, min(500.0, x))))


class PoseEstimator:
    """Runs a YOLO-pose HEF on person crops via Hailo NPU.

    Supports both the 9-output yolov8s_pose layout (Hailo Model Zoo v2.14+)
    and the legacy single-output layout. Layout is detected automatically in
    ``load()``.

    Lifecycle: ``load()`` → many ``estimate()`` calls → ``close()``.
    """

    def __init__(
        self,
        hef_path: Path,
        confidence_threshold: float = 0.05,
    ) -> None:
        if not HAILO_AVAILABLE:
            raise ImportError("hailo_platform required — run on RPi5 with HailoRT")
        self._hef_path = hef_path
        self._confidence_threshold = confidence_threshold

        self._device: Any = None
        self._infer_model: Any = None
        self._configured: Any = None
        self._exit_stack: Any = None
        self._input_h: int = 640
        self._input_w: int = 640

        # Multi-output path (yolov8s_pose — 9 tensors)
        self._multi_output: bool = False
        self._out_bufs: dict[str, Any] = {}  # name → ndarray

        # Single-output legacy path
        self._output_buf: Any = None

    def load(self, device: Any = None, scheduled: bool = False) -> None:
        """Open HEF, detect output layout, allocate per-output buffers.

        Pass an already-open VDevice as `device` to share it with other models.
        When `device` is None a new VDevice is created and owned by this instance.
        Set `scheduled=True` when the VDevice uses ROUND_ROBIN scheduler.
        """
        import numpy as np  # type: ignore[import]

        self._owns_device = device is None
        self._device = VDevice() if device is None else device
        self._infer_model = self._device.create_infer_model(str(self._hef_path))
        self._infer_model.set_batch_size(1)
        self._infer_model.input().set_format_type(FormatType.FLOAT32)

        input_info = self._infer_model.input()
        shape = input_info.shape  # (H, W, C)
        self._input_h = int(shape[0])
        self._input_w = int(shape[1])

        outputs = list(self._infer_model.outputs)
        self._multi_output = len(outputs) > 1

        if self._multi_output:
            # yolov8s_pose: 9 outputs — one buffer per output, keyed by name.
            for o in outputs:
                o.set_format_type(FormatType.FLOAT32)
                self._out_bufs[o.name] = np.empty(tuple(int(d) for d in o.shape), dtype=np.float32)
            logger.info(
                "Pose HEF loaded: %s (multi-output, %d tensors, input %dx%d)",
                self._hef_path.name,
                len(outputs),
                self._input_w,
                self._input_h,
            )
        else:
            # Legacy single-output layout.
            outputs[0].set_format_type(FormatType.FLOAT32)
            output_shape = tuple(int(d) for d in outputs[0].shape)
            self._output_buf = np.empty(output_shape, dtype=np.float32)
            logger.info(
                "Pose HEF loaded: %s (single-output shape=%s, input %dx%d)",
                self._hef_path.name,
                output_shape,
                self._input_w,
                self._input_h,
            )

        self._exit_stack = contextlib.ExitStack()
        self._configured = self._exit_stack.enter_context(self._infer_model.configure())
        if not scheduled:
            activate_result = self._configured.activate()
            if hasattr(activate_result, "__enter__"):
                self._exit_stack.enter_context(activate_result)

    def estimate(self, frame: Any, bbox: tuple[float, float, float, float]) -> Keypoints | None:
        """Extract 17 COCO keypoints for a single person crop.

        ``frame`` is the full BGR frame; ``bbox`` is normalised (x1,y1,x2,y2).
        Returns ``None`` when the crop is empty or no detection clears the
        confidence threshold.
        """
        import cv2  # type: ignore[import]
        import numpy as np  # type: ignore[import]

        if self._configured is None:
            raise RuntimeError("Call load() before estimate()")

        h, w = frame.shape[:2]
        x1, y1, x2, y2 = bbox

        # Expand the crop with padding so the person occupies ~40–65% of the
        # 640×640 HEF input rather than 100%.  Tight crops suppress the
        # objectness head (confidence≈0 for all cells) because no anchor
        # "fits" a person that fills the entire image; with padding the model
        # sees a normally-sized person and returns a valid confidence signal
        # that selects the correct cell for keypoint decoding.
        bw, bh = x2 - x1, y2 - y1
        x1 = max(0.0, x1 - bw * 0.75)
        y1 = max(0.0, y1 - bh * 0.75)
        x2 = min(1.0, x2 + bw * 0.75)
        y2 = min(1.0, y2 + bh * 0.75)

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

        bindings = self._configured.create_bindings()

        if self._multi_output:
            # Hailo Model Zoo models: calibrated on [0, 1] input.
            input_tensor = np.ascontiguousarray(resized.astype(np.float32) / 255.0)
            bindings.input().set_buffer(input_tensor)
            for name, buf in self._out_bufs.items():
                bindings.output(name).set_buffer(buf)
            self._configured.run([bindings], timeout=1000)
            kp_pts = self._decode_multi()
        else:
            # Legacy: calibrated on [0, 255].
            input_tensor = np.ascontiguousarray(resized.astype(np.float32))
            bindings.input().set_buffer(input_tensor)
            bindings.output().set_buffer(self._output_buf)
            self._configured.run([bindings], timeout=1000)
            kp_pts = self._decode_single(self._output_buf)

        if kp_pts is None:
            return None

        # Convert crop-relative [0, 1] → frame-normalised [0, 1].
        points: list[tuple[float, float, float]] = [
            (
                (px1 + kp_x * crop_w) / w,
                (py1 + kp_y * crop_h) / h,
                vis,
            )
            for kp_x, kp_y, vis in kp_pts
        ]
        return Keypoints(points=points)

    def _decode_multi(self) -> list[tuple[float, float, float]] | None:
        """Decode yolov8s_pose 9-tensor output → 17 crop-relative [0,1] keypoints.

        Groups tensors by spatial size.  For each scale picks the grid cell
        with highest score.  The best cell across all three scales provides
        the keypoints.

        Channel legend (confirmed by HEF diagnostic):
          [H, W, 64]  DFL bbox — ignored.
          [H, W,  1]  confidence — sigmoid already applied by Hailo.
                      NOTE: always 0.0 when input is a tight person crop (person
                      fills the entire 640×640 — no anchor matches).  Fallback:
                      use mean keypoint visibility as proxy confidence.
          [H, W, 51]  keypoints — standard YOLOv8 grid-relative encoding:
                      x_norm = (gx + sigmoid(raw_x)) * stride / input_w
                      y_norm = (gy + sigmoid(raw_y)) * stride / input_h
                      Visibility is a raw logit → sigmoid.
        """
        import numpy as np  # type: ignore[import]

        # Group output buffers by spatial dimensions (H, W).
        by_scale: dict[tuple[int, int], dict[int, Any]] = {}
        for _name, buf in self._out_bufs.items():
            if buf.ndim != 3:
                continue
            bh, bw, bc = buf.shape
            by_scale.setdefault((bh, bw), {})[bc] = buf

        best_score = 0.0
        actual_max = 0.0
        best_kpts_raw: Any = None
        best_gx = 0
        best_gy = 0
        best_sh = 80  # scale height used to compute stride

        for (sh, sw), tensors in by_scale.items():
            kpts_map = tensors.get(51)  # [H, W, 51] — keypoints
            if kpts_map is None:
                continue

            conf_map = tensors.get(1)  # [H, W, 1]
            if conf_map is not None and float(conf_map.max()) > 1e-6:
                # Padded crop: confidence has signal — use the highest-confidence cell.
                conf = conf_map[..., 0]
                if float(conf.max()) > 1.01:
                    conf = 1.0 / (1.0 + np.exp(-conf.clip(-500, 500)))
                idx = int(np.argmax(conf))
                gy, gx = divmod(idx, sw)
                score = float(conf[gy, gx])
            else:
                # Tight crop fallback (padding couldn't prevent conf=0, e.g. person
                # fills full frame vertically).  Use centre cell as stable fallback.
                gy, gx = sh // 2, sw // 2
                vis_logits = kpts_map[gy, gx].reshape(NUM_KEYPOINTS, 3)[:, 2]
                score = float((1.0 / (1.0 + np.exp(-vis_logits.clip(-500, 500)))).mean())

            actual_max = max(actual_max, score)
            if score > best_score:
                best_score = score
                best_kpts_raw = kpts_map[gy, gx].copy()  # [51]
                best_gx = gx
                best_gy = gy
                best_sh = sh

        if best_kpts_raw is None or best_score < self._confidence_threshold:
            channels_per_scale = {k: sorted(v.keys()) for k, v in by_scale.items()}
            logger.warning(
                "Pose _decode_multi: no tensor above threshold=%.3f (actual_max=%.4f), "
                "scales=%s channels_per_scale=%s",
                self._confidence_threshold,
                actual_max,
                list(by_scale.keys()),
                channels_per_scale,
            )
            return None

        # Decode keypoints using standard YOLOv8 grid-relative formula:
        #   x_norm = (gx + sigmoid(raw_x)) * stride / input_w
        # stride = input_h // sh (e.g. 640//40 = 16 for the medium scale).
        # For a tight crop where the person is centred the naive sigmoid-only
        # formula also gives ~0.5, but for padded or off-centre crops it
        # produces a visible horizontal/vertical offset — grid-relative is
        # always correct.
        stride = self._input_h // best_sh
        kps = best_kpts_raw.reshape(NUM_KEYPOINTS, 3)
        points: list[tuple[float, float, float]] = []
        for kp_x_raw, kp_y_raw, kp_v in kps:
            nx = (best_gx + _sigmoid(float(kp_x_raw))) * stride / self._input_w
            ny = (best_gy + _sigmoid(float(kp_y_raw))) * stride / self._input_h
            vis = _sigmoid(float(kp_v))
            points.append((nx, ny, vis))
        return points

    def _decode_single(self, raw: Any) -> list[tuple[float, float, float]] | None:
        """Decode legacy single-output [num_anchors, 56] layout."""
        import numpy as np  # type: ignore[import]

        if raw.ndim == 2 and raw.shape[0] == _POSE_VECTOR_LEN:
            raw = raw.T
        if raw.ndim != 2 or raw.shape[1] != _POSE_VECTOR_LEN:
            logger.debug("Unexpected single-output pose shape: %s", raw.shape)
            return None

        confs = raw[:, 4]
        best_idx = int(np.argmax(confs))
        if confs[best_idx] < self._confidence_threshold:
            return None

        kps = raw[best_idx, 5:].reshape(NUM_KEYPOINTS, 3)
        return [
            (
                float(kp_x) / self._input_w,
                float(kp_y) / self._input_h,
                float(kp_v),
            )
            for kp_x, kp_y, kp_v in kps
        ]

    def close(self) -> None:
        if self._exit_stack is not None:
            self._exit_stack.close()
            self._exit_stack = None
        self._configured = None
        self._infer_model = None
        if self._device is not None and getattr(self, "_owns_device", True):
            self._device.release()
        self._device = None
