"""Unit tests for face.py crop helpers and cosine_similarity.

FaceRecognizer itself requires hailo_platform so it is not tested here.
These tests cover the pure-Python / cv2 utilities that run on any machine.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

# cv2 is not installed in CI/dev — provide a minimal stub so lazy `import cv2`
# inside face.py function bodies finds the mock via sys.modules.
if "cv2" not in sys.modules:

    def _np_resize(img: np.ndarray, size: tuple[int, int]) -> np.ndarray:
        """Nearest-neighbour resize using numpy — mirrors cv2.resize(img, (w, h))."""
        out_w, out_h = size
        in_h, in_w = img.shape[:2]
        ys = np.round(np.linspace(0, in_h - 1, out_h)).astype(int)
        xs = np.round(np.linspace(0, in_w - 1, out_w)).astype(int)
        return img[ys[:, None], xs[None, :]]

    _cv2_mock = MagicMock()
    _cv2_mock.resize.side_effect = _np_resize
    _cv2_mock.cvtColor.side_effect = lambda img, code: img
    _cv2_mock.COLOR_BGR2RGB = 4
    sys.modules["cv2"] = _cv2_mock

from pathlib import Path

from hub.edge.cv.face import (
    COSINE_KNOWN_THRESHOLD,
    INPUT_H,
    INPUT_W,
    FaceRecognizer,
    _coerce_to_templates,
    cosine_similarity,
    crop_face_from_bbox,
    crop_face_from_keypoints,
)
from hub.edge.cv.pose import Keypoints


def _frame(h: int = 480, w: int = 640) -> np.ndarray:
    return np.full((h, w, 3), 128, dtype=np.uint8)


def _face_kps(cx: float = 0.5, cy: float = 0.3) -> Keypoints:
    """17-point Keypoints with 5 high-confidence face points around (cx, cy)."""
    pts: list[tuple[float, float, float]] = [(0.5, 0.5, 0.9)] * 17
    # COCO indices 0-4: nose, left_eye, right_eye, left_ear, right_ear
    pts[0] = (cx, cy + 0.02, 0.9)
    pts[1] = (cx - 0.03, cy - 0.02, 0.9)
    pts[2] = (cx + 0.03, cy - 0.02, 0.9)
    pts[3] = (cx - 0.06, cy, 0.9)
    pts[4] = (cx + 0.06, cy, 0.9)
    return Keypoints(points=pts)


# ── cosine_similarity ────────────────────────────────────────────────────────


def test_cosine_identical() -> None:
    v = [1.0, 0.0, 0.0]
    assert cosine_similarity(v, v) == pytest.approx(1.0, abs=1e-6)


def test_cosine_orthogonal() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0, abs=1e-6)


def test_cosine_opposite() -> None:
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0, abs=1e-6)


def test_cosine_zero_vector_no_raise() -> None:
    result = cosine_similarity([0.0, 0.0], [1.0, 0.0])
    assert result == pytest.approx(0.0, abs=1e-6)


def test_cosine_arbitrary() -> None:
    a = [3.0, 4.0]
    b = [4.0, 3.0]
    expected = (3 * 4 + 4 * 3) / (5.0 * 5.0)
    assert cosine_similarity(a, b) == pytest.approx(expected, abs=1e-6)


# ── crop_face_from_bbox ──────────────────────────────────────────────────────


def test_bbox_crop_output_shape() -> None:
    crop = crop_face_from_bbox(_frame(), (0.2, 0.1, 0.8, 0.9))
    assert crop is not None
    assert crop.shape == (INPUT_H, INPUT_W, 3)


def test_bbox_crop_zero_size_returns_none() -> None:
    assert crop_face_from_bbox(_frame(), (0.5, 0.5, 0.5, 0.5)) is None


def test_bbox_crop_inverted_returns_none() -> None:
    # x2 < x1 → bw < 0
    assert crop_face_from_bbox(_frame(), (0.8, 0.8, 0.2, 0.2)) is None


def test_bbox_crop_out_of_frame_clips_gracefully() -> None:
    # bbox extends well beyond [0, 1]
    crop = crop_face_from_bbox(_frame(), (-0.5, -0.5, 1.5, 1.5))
    assert crop is not None
    assert crop.shape == (INPUT_H, INPUT_W, 3)


def test_bbox_crop_uses_top_fraction() -> None:
    """Crop region starts at y1 of the person bbox (face is at the top)."""
    h, w = 480, 640
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    # Fill top half white so we can verify we're cropping from there
    frame[: h // 2, :] = 255
    bbox = (0.0, 0.0, 1.0, 1.0)
    crop = crop_face_from_bbox(frame, bbox)
    assert crop is not None
    # The crop should contain mostly white pixels (from top-FACE_TOP_FRAC of the image)
    white_ratio = float(np.mean(crop > 200))
    assert white_ratio > 0.5, f"Expected top-crop to be mostly white, got {white_ratio:.2f}"


# ── crop_face_from_keypoints ─────────────────────────────────────────────────


def test_kps_crop_output_shape() -> None:
    crop = crop_face_from_keypoints(_frame(), _face_kps(), (0.3, 0.1, 0.7, 0.9))
    assert crop is not None
    assert crop.shape == (INPUT_H, INPUT_W, 3)


def test_kps_crop_low_confidence_fallback_to_bbox() -> None:
    """All face keypoints below threshold → falls back to bbox heuristic."""
    low_conf_pts: list[tuple[float, float, float]] = [(0.5, 0.5, 0.1)] * 17
    kps = Keypoints(points=low_conf_pts)
    bbox = (0.2, 0.1, 0.8, 0.9)
    crop_kps = crop_face_from_keypoints(_frame(), kps, bbox)
    crop_bbox = crop_face_from_bbox(_frame(), bbox)
    assert crop_kps is not None
    assert crop_bbox is not None
    assert crop_kps.shape == crop_bbox.shape


def test_kps_crop_too_few_points_fallback() -> None:
    """Fewer than 5 keypoints → falls back to bbox heuristic."""
    kps = Keypoints(points=[(0.5, 0.3, 0.9)] * 3)
    crop = crop_face_from_keypoints(_frame(), kps, (0.2, 0.1, 0.8, 0.9))
    assert crop is not None
    assert crop.shape == (INPUT_H, INPUT_W, 3)


def test_kps_crop_no_points_attr_fallback() -> None:
    """Keypoints with no ``points`` attribute → falls back to bbox heuristic."""

    class _FakeKps:
        pass

    crop = crop_face_from_keypoints(_frame(), _FakeKps(), (0.2, 0.1, 0.8, 0.9))
    assert crop is not None
    assert crop.shape == (INPUT_H, INPUT_W, 3)


def test_kps_crop_different_region_than_bbox() -> None:
    """Valid face keypoints should produce a different crop than pure bbox heuristic."""
    h, w = 480, 640
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    # Paint a bright square exactly where the keypoints cluster (cx=0.5, cy=0.3)
    px, py = int(0.5 * w), int(0.3 * h)
    frame[py - 20 : py + 20, px - 20 : px + 20] = 200

    kps = _face_kps(cx=0.5, cy=0.3)
    bbox = (0.2, 0.7, 0.8, 1.0)  # bbox at bottom — far from keypoints
    crop_kps = crop_face_from_keypoints(frame, kps, bbox)
    crop_bbox = crop_face_from_bbox(frame, bbox)

    assert crop_kps is not None and crop_bbox is not None
    # Keypoint crop should contain some bright pixels; bbox crop (at bottom) should not
    assert float(np.mean(crop_kps > 150)) > float(np.mean(crop_bbox > 150))


# --- Embeddings format coercion ---------------------------------------------


def test_coerce_legacy_single_vector_to_template_list() -> None:
    """Old-format {name: list[float]} is read as single-template list[list[float]]."""
    raw = {"alice": [0.1] * 512}
    out = _coerce_to_templates(raw)
    assert "alice" in out
    assert len(out["alice"]) == 1
    assert len(out["alice"][0]) == 512


def test_coerce_new_format_preserved() -> None:
    raw = {"bob": [[0.1] * 512, [0.2] * 512]}
    out = _coerce_to_templates(raw)
    assert len(out["bob"]) == 2


def test_coerce_drops_empty_and_bad_root() -> None:
    assert _coerce_to_templates({"x": []}) == {}
    assert _coerce_to_templates("not a dict") == {}  # type: ignore[arg-type]


# --- Multi-template matching ------------------------------------------------


def _make_recognizer(enrolled: dict[str, list[list[float]]]) -> FaceRecognizer:
    """Build a FaceRecognizer without loading any HEF."""
    r = FaceRecognizer(Path("/nonexistent.hef"))
    r._enrolled = enrolled
    return r


def _unit(*values: float) -> list[float]:
    """L2-normalize a 512-d vector built from a repeating pattern."""
    arr = np.array(
        list(values) * (512 // len(values)) + [0.0] * (512 % len(values)), dtype="float32"
    )
    arr /= np.linalg.norm(arr)
    return arr.tolist()


def test_match_picks_best_template_per_identity() -> None:
    """An identity with multiple templates wins when ANY template is close enough."""
    near = _unit(1.0, 0.0)
    far = _unit(0.0, 1.0)
    query = _unit(1.0, 0.001)  # close to `near`, far from `far`

    # alice has only a bad template; bob has one bad and one matching the query.
    r = _make_recognizer({"alice": [far], "bob": [far, near]})
    name, sim = r._match(query)
    assert name == "bob"
    assert sim >= COSINE_KNOWN_THRESHOLD


def test_match_uses_max_not_mean_across_templates() -> None:
    """A perfect match on one template beats a mediocre mean across many."""
    perfect = _unit(1.0, 0.0)
    distractors = [_unit(0.0, 1.0) for _ in range(4)]  # mean would be far from query
    query = _unit(1.0, 0.0)

    r = _make_recognizer({"target": [*distractors, perfect]})
    name, sim = r._match(query)
    assert name == "target"
    assert sim > 0.99  # the perfect template should win outright


def test_match_unknown_when_no_template_close() -> None:
    far = _unit(0.0, 1.0)
    query = _unit(1.0, 0.0)
    r = _make_recognizer({"alice": [far]})
    name, _sim = r._match(query)
    assert name == "unknown"


# --- Diverse template selection ---------------------------------------------


def test_select_diverse_templates_returns_all_when_n_le_k() -> None:
    from hub.backend.routes.enroll import select_diverse_templates

    samples = np.random.RandomState(0).randn(3, 512).astype("float32")
    samples /= np.linalg.norm(samples, axis=1, keepdims=True)
    out = select_diverse_templates(samples, k=5)
    assert out.shape == (3, 512)


def test_select_diverse_templates_picks_spread() -> None:
    """Given two clusters and one outlier, picking K=3 should hit all three regions."""
    from hub.backend.routes.enroll import select_diverse_templates

    rng = np.random.RandomState(1)

    # Three well-separated directions in 512-d
    def cluster(seed: int) -> np.ndarray:
        v = rng.randn(512).astype("float32")
        v /= np.linalg.norm(v)
        # 5 copies with tiny jitter around v
        block = np.tile(v, (5, 1)) + 0.01 * rng.randn(5, 512).astype("float32")
        block /= np.linalg.norm(block, axis=1, keepdims=True)
        return block

    samples = np.concatenate([cluster(0), cluster(1), cluster(2)], axis=0)
    templates = select_diverse_templates(samples, k=3)
    assert templates.shape == (3, 512)

    # The three picked templates should not be near-duplicates: max pairwise
    # cosine sim should be well below 1 (they came from different clusters).
    sims = templates @ templates.T
    np.fill_diagonal(sims, 0.0)
    assert float(sims.max()) < 0.9


def test_select_diverse_templates_handles_empty() -> None:
    from hub.backend.routes.enroll import select_diverse_templates

    empty = np.zeros((0, 512), dtype="float32")
    out = select_diverse_templates(empty, k=5)
    assert out.shape == (0, 512)


def test_samples_save_load_roundtrip(tmp_path: Path) -> None:
    """Regression: numpy.savez_compressed auto-appends '.npz' to path args,
    which previously broke atomic-replace on samples.npz.tmp."""
    from hub.backend.routes.enroll import _load_samples, _save_samples

    arr = np.random.RandomState(0).randn(7, 512).astype("float32")
    target = tmp_path / "person" / "samples.npz"
    _save_samples(target, arr)
    assert target.exists()
    # The tmp must be gone (replace consumed it) and no stray .npz.tmp.npz.
    assert not (target.parent / "samples.npz.tmp").exists()
    assert not (target.parent / "samples.npz.tmp.npz").exists()
    loaded = _load_samples(target)
    np.testing.assert_array_equal(loaded, arr)
