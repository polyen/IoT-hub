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
    [H, W, 51]  17 COCO keypoints × (x, y, visibility) — Ultralytics
                YOLOv8-pose export head convention (see ``Pose.kpts_decode``):
                    kp_x_pixel = (raw_x * 2.0 + (gx - 0.5)) * stride
                    kp_y_pixel = (raw_y * 2.0 + (gy - 0.5)) * stride
                    visibility = sigmoid(raw_v)
                ``raw_x`` / ``raw_y`` are **unbounded floats** (no sigmoid
                pre-applied), allowing a keypoint to land anywhere in the
                640×640 input — not just inside the predicting cell.
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

        # Diagnostic logging — emit raw kpt/conf statistics every N decodes so
        # the operator can verify the model is actually responding to body
        # pose rather than feeding noise into a fixed-pattern skeleton.
        self._diag_count: int = 0
        self._diag_every: int = 120

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

        # Letterbox to 640×640 — preserve aspect ratio with gray (114) padding.
        # yolov8s_pose from Hailo Model Zoo is calibrated on letterboxed input;
        # a plain ``cv2.resize`` to a non-square crop stretches the person and
        # the model fails to recognise it (confidence collapses to zero, then
        # the centre-cell fallback returns near-constant noise and the rendered
        # skeleton stops responding to the actual body pose — only scales with
        # the bbox).  Padding colour 114 matches the HMZ preprocessing recipe.
        scale = min(self._input_w / crop_w, self._input_h / crop_h)
        new_w = max(1, int(round(crop_w * scale)))
        new_h = max(1, int(round(crop_h * scale)))
        resized_inner = cv2.resize(crop_rgb, (new_w, new_h))
        padded = np.full((self._input_h, self._input_w, 3), 114, dtype=np.uint8)
        pad_left = (self._input_w - new_w) // 2
        pad_top = (self._input_h - new_h) // 2
        padded[pad_top : pad_top + new_h, pad_left : pad_left + new_w] = resized_inner

        bindings = self._configured.create_bindings()

        if self._multi_output:
            # Hailo Model Zoo models: calibrated on [0, 1] input.
            input_tensor = np.ascontiguousarray(padded.astype(np.float32) / 255.0)
            bindings.input().set_buffer(input_tensor)
            for name, buf in self._out_bufs.items():
                bindings.output(name).set_buffer(buf)
            self._configured.run([bindings], timeout=1000)
            kp_pts = self._decode_multi()
        else:
            # Legacy: calibrated on [0, 255].
            input_tensor = np.ascontiguousarray(padded.astype(np.float32))
            bindings.input().set_buffer(input_tensor)
            bindings.output().set_buffer(self._output_buf)
            self._configured.run([bindings], timeout=1000)
            kp_pts = self._decode_single(self._output_buf)

        if kp_pts is None:
            return None

        # Inverse-letterbox each keypoint, then map back to the full frame.
        # ``kp_pts`` are in [0, 1] relative to the 640×640 *letterboxed* input;
        # undo padding/scale to get coords in the original crop, then add the
        # bbox offset.
        points: list[tuple[float, float, float]] = []
        for kp_xn, kp_yn, vis in kp_pts:
            px_lb = kp_xn * self._input_w
            py_lb = kp_yn * self._input_h
            px_in_crop = (px_lb - pad_left) / scale
            py_in_crop = (py_lb - pad_top) / scale
            points.append(
                (
                    (px1 + px_in_crop) / w,
                    (py1 + py_in_crop) / h,
                    vis,
                )
            )
        return Keypoints(points=points)

    def _decode_multi(self) -> list[tuple[float, float, float]] | None:
        """Decode yolov8s_pose 9-tensor output → 17 crop-relative [0,1] keypoints.

        Groups tensors by spatial size.  For each scale picks the grid cell
        with highest score.  The best cell across all three scales provides
        the keypoints.

        Channel legend (confirmed by HEF diagnostic):
          [H, W, 64]  DFL bbox — ignored.
          [H, W,  1]  confidence — sigmoid already applied by Hailo.
                      With a letterboxed crop (gray-padded so aspect ratio is
                      preserved) and ~75% padding around the bbox this head
                      has a real signal and selects the right cell.  Tight or
                      stretched crops collapse the head to 0 → centre-cell
                      fallback returns a fixed-pattern skeleton.
          [H, W, 51]  keypoints — Ultralytics YOLOv8-pose convention
                      (``Pose.kpts_decode`` in ultralytics/nn/modules/head.py):
                          kp_x_pixel = (raw_x * 2.0 + (gx - 0.5)) * stride
                          kp_y_pixel = (raw_y * 2.0 + (gy - 0.5)) * stride
                          visibility = sigmoid(raw_v)
                      ``raw_x``/``raw_y`` are **unbounded** (no sigmoid
                      pre-applied), so a keypoint can land anywhere in the
                      crop — not only inside the predicting cell.  The
                      previously used ``(gx + sigmoid(raw_x)) * stride``
                      formula is the *bbox-centre* encoding (bounds the
                      offset to one cell), which collapsed all 17 keypoints
                      into the chosen cell ≈ centre of the bbox.
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

        # Ultralytics YOLOv8-pose decoding (no sigmoid on coords, *2 factor):
        #   kp_x_pixel = (raw_x * 2.0 + (gx - 0.5)) * stride
        # stride = input_h // sh (e.g. 640//40 = 16 for the P4 scale).
        # raw_x/raw_y are unbounded — this is what lets the 17 keypoints
        # spread across the body instead of clustering inside a single cell.
        stride = self._input_h / best_sh
        kps = best_kpts_raw.reshape(NUM_KEYPOINTS, 3)
        points: list[tuple[float, float, float]] = []
        for kp_x_raw, kp_y_raw, kp_v in kps:
            nx = (float(kp_x_raw) * 2.0 + (best_gx - 0.5)) * stride / self._input_w
            ny = (float(kp_y_raw) * 2.0 + (best_gy - 0.5)) * stride / self._input_h
            vis = _sigmoid(float(kp_v))
            points.append((nx, ny, vis))

        # Periodic diagnostic: log raw kpt/conf statistics so a stuck pipeline
        # is visible from the logs (e.g. flat raw range, conf always 0, only
        # the centre cell ever winning).
        self._diag_count += 1
        if self._diag_count == 1 or self._diag_count % self._diag_every == 0:
            xs = kps[:, 0].astype(np.float64)
            ys = kps[:, 1].astype(np.float64)
            logger.info(
                "Pose decode diag #%d: best_sh=%d gx=%d gy=%d score=%.4f "
                "raw_kpx=[%.2f,%.2f] raw_kpy=[%.2f,%.2f] actual_max=%.4f",
                self._diag_count,
                best_sh,
                best_gx,
                best_gy,
                best_score,
                float(xs.min()),
                float(xs.max()),
                float(ys.min()),
                float(ys.max()),
                actual_max,
            )

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
