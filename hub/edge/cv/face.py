"""ArcFace face recognition wrapper (Stage 4 of the CV cascade).

Runs the ArcFace HEF on Hailo NPU to extract a 512-d embedding from a face
crop, then compares against enrolled embeddings via cosine similarity.

Throttle: at most 1 inference per second per track_id (set in pipeline.py;
the throttle state lives here so that re-creating the recognizer across
SIGHUP reloads doesn't reset the per-track cool-down).

Face crop strategy (no pose available): the top portion of the person bbox
is used as an approximate face region. When pose keypoints become available
(``Keypoints.points`` indices 0..4 = nose, eyes, ears), prefer
``crop_face_from_keypoints``.

T0 data: face frames live encrypted on ``/mnt/edge-data/frames/``. Recognition
does not persist frames by itself — use ``save_t0_frame`` from the pipeline
only when a confirmation flow requires it.
"""

from __future__ import annotations

import contextlib
import logging
import math
import pickle
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from hailo_platform import FormatType, VDevice  # type: ignore[import]

    HAILO_AVAILABLE = True
except ImportError:
    HAILO_AVAILABLE = False

# ArcFace decision thresholds (cosine similarity, range [-1, 1]).
# Tuned to be conservative — only confident matches are emitted as "known".
COSINE_KNOWN_THRESHOLD = 0.6
COSINE_UNKNOWN_THRESHOLD = 0.4

# Heuristic head/face box inside a person bbox when pose keypoints aren't
# available. Surveys of standing-figure crops suggest the head occupies the
# top ~25-30 % of the bbox height; we square it horizontally around the
# bbox centre. Tight enough for surveillance-distance crops, loose enough
# to survive small bbox jitter.
FACE_TOP_FRAC = 0.30
FACE_WIDTH_FRAC = 0.55

# ArcFace canonical input.
INPUT_H = 112
INPUT_W = 112

T0_FRAME_DIR = Path("/mnt/edge-data/frames")


@dataclass
class RecognitionResult:
    track_id: int
    identity: str
    similarity: float
    embedding: list[float]


def crop_face_from_bbox(
    frame: Any,
    person_bbox: tuple[float, float, float, float],
) -> Any | None:
    """Crop a face region from a person bbox using a fixed top/centre heuristic.

    ``person_bbox`` is normalized ``(x1, y1, x2, y2)``. Returns a BGR ``ndarray``
    (uint8) sized for ArcFace input, or None if the crop is empty.
    """
    import cv2  # type: ignore[import]
    import numpy as np  # type: ignore[import]

    h, w = frame.shape[:2]
    x1, y1, x2, y2 = person_bbox
    bw = max(0.0, x2 - x1)
    bh = max(0.0, y2 - y1)
    if bw <= 0 or bh <= 0:
        return None

    face_h = bh * FACE_TOP_FRAC
    face_w = bw * FACE_WIDTH_FRAC
    cx = (x1 + x2) / 2.0
    fy1 = y1
    fx1 = cx - face_w / 2.0
    fy2 = y1 + face_h
    fx2 = cx + face_w / 2.0

    px1 = max(0, int(fx1 * w))
    py1 = max(0, int(fy1 * h))
    px2 = min(w, int(fx2 * w))
    py2 = min(h, int(fy2 * h))
    if px2 <= px1 or py2 <= py1:
        return None
    crop = frame[py1:py2, px1:px2]
    if crop.size == 0:
        return None
    return cv2.resize(crop, (INPUT_W, INPUT_H)).astype(np.uint8)


def crop_face_from_keypoints(
    frame: Any,
    keypoints: Any,
    person_bbox: tuple[float, float, float, float],
) -> Any | None:
    """Crop a face region from COCO pose keypoints (nose, eyes, ears).

    Uses indices 0..4 of ``keypoints.points``.  The derived bbox is already a
    tight face region, so it is cropped directly — NOT forwarded through
    ``crop_face_from_bbox``, which would incorrectly apply the person-bbox
    top-fraction heuristic a second time.  Falls back to ``crop_face_from_bbox``
    when face keypoints are absent or have low confidence.
    """
    import cv2  # type: ignore[import]
    import numpy as np  # type: ignore[import]

    pts = getattr(keypoints, "points", None)
    if pts is None or len(pts) < 5:
        return crop_face_from_bbox(frame, person_bbox)

    face_pts = [(x, y, c) for (x, y, c) in pts[:5] if c > 0.3]
    if len(face_pts) < 2:
        return crop_face_from_bbox(frame, person_bbox)

    xs = np.array([p[0] for p in face_pts])
    ys = np.array([p[1] for p in face_pts])
    cx, cy = float(xs.mean()), float(ys.mean())
    extent = float(max(xs.max() - xs.min(), ys.max() - ys.min()))
    half = max(extent * 1.2, 0.05)  # pad 20 %, min 5 % of frame

    h, w = frame.shape[:2]
    px1 = max(0, int((cx - half) * w))
    py1 = max(0, int((cy - half) * h))
    px2 = min(w, int((cx + half) * w))
    py2 = min(h, int((cy + half) * h))
    if px2 <= px1 or py2 <= py1:
        return crop_face_from_bbox(frame, person_bbox)
    crop = frame[py1:py2, px1:px2]
    if crop.size == 0:
        return crop_face_from_bbox(frame, person_bbox)
    return cv2.resize(crop, (INPUT_W, INPUT_H)).astype(np.uint8)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb + 1e-8) if na > 0 and nb > 0 else 0.0


def _coerce_to_templates(raw: Any) -> dict[str, list[list[float]]]:
    """Normalize embeddings.pkl payload to ``{name: list[list[float]]}``.

    Legacy enrollments stored a single mean vector per identity
    (``{name: list[float]}``). New enrollments store K diverse templates
    (``{name: list[list[float]]}``). This shim keeps the recognizer agnostic
    to which writer last touched the file, so an upgrade can happen on the
    next enroll without a migration step.
    """
    out: dict[str, list[list[float]]] = {}
    if not isinstance(raw, dict):
        logger.warning("embeddings.pkl has non-dict root (%s) — ignoring", type(raw))
        return out
    for name, val in raw.items():
        if not isinstance(val, list) or not val:
            continue
        if isinstance(val[0], int | float):
            out[name] = [list(val)]
        else:
            out[name] = [list(t) for t in val]
    return out


class FaceRecognizer:
    """ArcFace HEF runner with cosine-sim matching against enrolled embeddings.

    Lifecycle: ``load()`` → many ``recognize_from_person_bbox()`` calls →
    ``close()``. The recognizer is safe to construct on dev machines without
    ``hailo_platform`` (raises ``ImportError`` only at ``load()`` time so the
    pipeline can degrade gracefully).
    """

    def __init__(
        self,
        hef_path: Path,
        embeddings_path: Path = Path("models/embeddings.pkl"),
    ) -> None:
        self._hef_path = hef_path
        self._embeddings_path = embeddings_path
        # Each entry holds up to K diverse templates (see
        # backend/routes/enroll.py:select_diverse_templates). The recognizer
        # takes the max cosine similarity across an identity's templates,
        # which handles pose/lighting variation better than matching against
        # a single mean. Legacy single-vector entries are upgraded on load.
        self._enrolled: dict[str, list[list[float]]] = {}
        self._last_inference: dict[int, float] = {}

        self._device: Any = None
        self._infer_model: Any = None
        self._configured: Any = None
        self._exit_stack: Any = None
        self._output_buf: Any = None

    def reload_embeddings(self) -> None:
        """(Re-)read enrolled templates from ``embeddings.pkl`` into memory.

        Called once from ``load()`` and again whenever the CV pipeline notices
        the file changed on disk (after ``/api/cv/enroll`` rewrites it). This
        is the *lightweight* reload path — it does NOT touch the Hailo HEF, so
        a fresh enrollment takes effect within one poll interval without a
        VDevice reconfigure or a dropped frame. In prod (CV as a host systemd
        service, container scaled to 0) the enroll route's ``docker kill
        SIGHUP cv`` never reaches us, so this on-disk poll is the *only* way a
        new enrollment is picked up without a full ``systemctl restart``.
        """
        if not self._embeddings_path.exists():
            logger.warning(
                "No embeddings file at %s — recognition will return 'unknown' only",
                self._embeddings_path,
            )
            self._enrolled = {}
            return
        try:
            with open(self._embeddings_path, "rb") as f:
                raw = pickle.load(f)  # noqa: S301 — local T0 file
            self._enrolled = _coerce_to_templates(raw)
            total_templates = sum(len(v) for v in self._enrolled.values())
            logger.info(
                "Loaded %d identities (%d templates total) from %s",
                len(self._enrolled),
                total_templates,
                self._embeddings_path,
            )
        except (EOFError, pickle.UnpicklingError):
            logger.warning(
                "Embeddings file %s is empty or corrupt — recognition will return 'unknown'",
                self._embeddings_path,
            )

    def load(self, device: Any = None, scheduled: bool = False) -> None:
        """Open the ArcFace HEF and load enrolled embeddings (if any).

        Pass an already-open VDevice as `device` to share it with other models.
        When `device` is None a new VDevice is created and owned by this instance.
        Set `scheduled=True` when the VDevice uses ROUND_ROBIN scheduler.

        Mirrors ``HailoDetector.load`` — HailoRT 4.17+ ``create_infer_model``
        API with ``ExitStack`` keeping the configured context alive. Missing
        embeddings file is non-fatal: the recognizer still extracts vectors,
        just can't classify them yet.
        """
        if not HAILO_AVAILABLE:
            raise ImportError(
                "hailo_platform not installed — run on RPi5 with HailoRT. "
                "See scripts/edge-bootstrap.sh"
            )
        import numpy as np  # type: ignore[import]

        self.reload_embeddings()

        self._owns_device = device is None
        self._device = VDevice() if device is None else device
        self._infer_model = self._device.create_infer_model(str(self._hef_path))
        self._infer_model.set_batch_size(1)
        self._infer_model.input().set_format_type(FormatType.FLOAT32)
        self._infer_model.output().set_format_type(FormatType.FLOAT32)

        output_info = self._infer_model.output()
        output_shape = tuple(int(d) for d in output_info.shape)

        self._exit_stack = contextlib.ExitStack()
        self._configured = self._exit_stack.enter_context(self._infer_model.configure())
        if not scheduled:
            activate_result = self._configured.activate()
            if hasattr(activate_result, "__enter__"):
                self._exit_stack.enter_context(activate_result)
        self._output_buf = np.empty(output_shape, dtype=np.float32)

        logger.info("ArcFace HEF loaded: %s (output shape=%s)", self._hef_path.name, output_shape)

    def embed_face(self, face_crop: Any) -> list[float]:
        """Run ArcFace on a 112×112 BGR crop. Returns L2-normalized 512-d embedding.

        Public so enrollment scripts can reuse the same preprocessing+inference
        path as runtime recognition without touching internals.
        """
        import cv2  # type: ignore[import]
        import numpy as np  # type: ignore[import]

        if self._configured is None:
            raise RuntimeError("Call load() before embed_face()")

        rgb = cv2.cvtColor(face_crop, cv2.COLOR_BGR2RGB)
        # ArcFace standard preprocessing: ((px / 255) - 0.5) / 0.5 → [-1, 1].
        # We pass float32 in [0, 255]; HailoRT auto-quantizes per HEF params.
        input_tensor = np.ascontiguousarray(rgb.astype(np.float32))

        bindings = self._configured.create_bindings()
        bindings.input().set_buffer(input_tensor)
        bindings.output().set_buffer(self._output_buf)
        self._configured.run([bindings], timeout=1000)

        emb = self._output_buf.reshape(-1).astype(np.float32)
        norm = float(np.linalg.norm(emb))
        if norm > 0:
            emb = emb / norm
        return emb.tolist()  # type: ignore[no-any-return]

    def _match(self, embedding: list[float]) -> tuple[str, float]:
        """Return (identity, similarity). 'unknown' if best sim < threshold.

        Score for each identity is the MAX cosine sim across its templates —
        not the mean. With K templates spanning pose/lighting, the closest
        template wins; this beats matching against a single averaged anchor
        when the query face is off-frontal or under different illumination.
        """
        best_name = "unknown"
        best_sim = 0.0
        for name, templates in self._enrolled.items():
            for template in templates:
                sim = cosine_similarity(embedding, template)
                if sim > best_sim:
                    best_sim = sim
                    best_name = name
        if best_sim < COSINE_UNKNOWN_THRESHOLD:
            return "unknown", best_sim
        if best_sim < COSINE_KNOWN_THRESHOLD:
            # Uncertain match — return the candidate name with "?" so the UI can
            # display it as amber and let the user confirm or correct it.
            return f"{best_name}?", best_sim
        return best_name, best_sim

    def get_throttle_state(self) -> dict[int, float]:
        """Return a snapshot of the per-track throttle timestamps."""
        return dict(self._last_inference)

    def set_throttle_state(self, state: dict[int, float]) -> None:
        """Restore per-track throttle timestamps (used after SIGHUP reload)."""
        self._last_inference = dict(state)

    def recognize_from_person_bbox(
        self,
        frame: Any,
        person_bbox: tuple[float, float, float, float],
        track_id: int,
        keypoints: Any | None = None,
    ) -> RecognitionResult | None:
        """Throttled recognition on a person crop.

        Returns ``None`` when the per-track cool-down has not elapsed, when the
        face crop is empty, or when the embedding fails. Returns a
        ``RecognitionResult`` with ``identity="unknown"`` when no enrolled
        embedding matches confidently — the caller decides whether to publish.

        When ``keypoints`` (pose stage output) are provided, uses the 5 face
        keypoints (nose, eyes, ears) for a tighter crop instead of the top-bbox
        heuristic.
        """
        now = time.monotonic()
        if now - self._last_inference.get(track_id, 0.0) < 1.0:
            return None
        self._last_inference[track_id] = now

        if keypoints is not None:
            crop = crop_face_from_keypoints(frame, keypoints, person_bbox)
        else:
            crop = crop_face_from_bbox(frame, person_bbox)
        if crop is None:
            return None

        try:
            embedding = self.embed_face(crop)
        except (RuntimeError, ValueError) as exc:
            logger.debug("ArcFace inference failed: %s", exc)
            return None

        identity, similarity = self._match(embedding)
        return RecognitionResult(
            track_id=track_id,
            identity=identity,
            similarity=similarity,
            embedding=embedding,
        )

    def save_t0_frame(self, frame: Any, track_id: int) -> Path | None:
        """Persist a frame to encrypted T0 storage. Returns the file path."""
        import cv2  # type: ignore[import]

        if not T0_FRAME_DIR.exists():
            return None
        out = T0_FRAME_DIR / f"track_{track_id}_{int(time.time())}.jpg"
        try:
            cv2.imwrite(str(out), frame)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to write T0 frame")
            return None
        return out

    def close(self) -> None:
        if self._exit_stack is not None:
            self._exit_stack.close()
            self._exit_stack = None
        self._configured = None
        self._infer_model = None
        if self._device is not None and getattr(self, "_owns_device", True):
            self._device.release()
        self._device = None
