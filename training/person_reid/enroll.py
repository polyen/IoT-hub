"""Enroll one identity into ``embeddings.pkl`` from one or many photos.

The script:

1. Detects a face in each photo with OpenCV's bundled Haar cascade
   (``haarcascade_frontalface_default.xml``) — fast and dependency-free.
   Falls back to using the whole image as the face when no face is found.
2. Resizes the face crop to 112 × 112 and runs ``current_face.hef`` on the
   Hailo NPU through ``FaceRecognizer.embed_face``.
3. Averages embeddings across all enrolled photos and L2-normalizes the mean.
4. Updates ``embeddings.pkl`` (``{name: list[float]}``) in place.

Usage::

    # Single photo
    uv run python -m training.person_reid.enroll \\
        --name vlad --photo path/to/face.jpg

    # Many photos for one person (recommended, 30–50 per person per §15)
    uv run python -m training.person_reid.enroll \\
        --name vlad --photos datasets/person_reid/known/person_01/

T0 reminder: photos under ``datasets/person_reid/`` are T0 — they must never
leave the edge/laptop. Pre-commit ``scripts/check_datasets.py`` blocks them
from the DVC remote; ``embeddings.pkl`` itself is T0-derived but stores only
512-d vectors, not pixels.
"""

from __future__ import annotations

import argparse
import logging
import pickle
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_HEF = Path("models/current_face.hef")
DEFAULT_OUT = Path("models/embeddings.pkl")
IMG_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp")
HAAR_MIN_SIZE = (40, 40)


def _load_face_cascade() -> Any:
    """Load OpenCV's bundled Haar cascade for frontal-face detection."""
    import cv2  # type: ignore[import]

    cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    cascade = cv2.CascadeClassifier(str(cascade_path))
    if cascade.empty():
        raise RuntimeError(f"Failed to load Haar cascade at {cascade_path}")
    return cascade


def _detect_face_bbox(image: Any, cascade: Any) -> tuple[int, int, int, int] | None:
    """Return absolute pixel ``(x1, y1, x2, y2)`` of the largest face, or None."""
    import cv2  # type: ignore[import]

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    faces = cascade.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=HAAR_MIN_SIZE,
    )
    if len(faces) == 0:
        return None
    x, y, w, h = max(faces, key=lambda f: int(f[2]) * int(f[3]))
    return int(x), int(y), int(x + w), int(y + h)


def _collect_photo_paths(photo: Path | None, photos_dir: Path | None) -> list[Path]:
    """Resolve --photo / --photos into a concrete file list."""
    if photo is not None:
        if not photo.is_file():
            sys.exit(f"--photo does not exist: {photo}")
        return [photo]
    assert photos_dir is not None
    if not photos_dir.is_dir():
        sys.exit(f"--photos must be a directory: {photos_dir}")
    paths = sorted(p for p in photos_dir.iterdir() if p.suffix.lower() in IMG_SUFFIXES)
    if not paths:
        sys.exit(f"No images ({IMG_SUFFIXES}) found in {photos_dir}")
    return paths


def enroll(
    name: str,
    photo_paths: list[Path],
    hef_path: Path,
    out_path: Path,
) -> None:
    """Build a single averaged embedding for ``name`` and persist to ``out_path``."""
    import cv2  # type: ignore[import]
    import numpy as np  # type: ignore[import]

    from hub.edge.cv.face import INPUT_H, INPUT_W, FaceRecognizer

    if not hef_path.exists():
        sys.exit(
            f"ArcFace HEF not found at {hef_path}. Compile via training/convert_to_hef.py "
            "and place it at the deploy symlink target before enrolling."
        )

    cascade = _load_face_cascade()
    # We don't need enrolled comparisons during enrollment — point at a path
    # that doesn't exist so FaceRecognizer.load() skips reading any pkl.
    recognizer = FaceRecognizer(hef_path, embeddings_path=Path("/dev/null"))
    recognizer.load()

    embeddings: list[list[float]] = []
    with_face = 0
    try:
        for photo_path in photo_paths:
            img = cv2.imread(str(photo_path))
            if img is None:
                print(f"  [skip] {photo_path.name}: cannot read")
                continue
            bbox = _detect_face_bbox(img, cascade)
            if bbox is None:
                print(f"  [warn] {photo_path.name}: no face detected, using whole image")
                crop = cv2.resize(img, (INPUT_W, INPUT_H))
            else:
                x1, y1, x2, y2 = bbox
                crop = cv2.resize(img[y1:y2, x1:x2], (INPUT_W, INPUT_H))
                with_face += 1
            try:
                emb = recognizer.embed_face(crop)
            except (RuntimeError, ValueError) as exc:
                print(f"  [skip] {photo_path.name}: embed failed ({exc})")
                continue
            embeddings.append(emb)
            print(f"  [ok]   {photo_path.name}")
    finally:
        recognizer.close()

    if not embeddings:
        sys.exit("No usable photos — nothing enrolled.")

    arr = np.asarray(embeddings, dtype=np.float32)
    mean = arr.mean(axis=0)
    norm = float(np.linalg.norm(mean))
    if norm <= 0:
        sys.exit("Averaged embedding has zero norm — aborting (likely all-zero outputs).")
    mean = mean / norm
    mean_list: list[float] = mean.tolist()

    enrolled: dict[str, list[float]] = {}
    if out_path.exists():
        with open(out_path, "rb") as f:
            enrolled = pickle.load(f)  # noqa: S301 — local T0-derived file
    enrolled[name] = mean_list

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        pickle.dump(enrolled, f)

    print(
        f"\nEnrolled '{name}' from {len(embeddings)} photo(s) "
        f"({with_face} with detected face) → {out_path}"
    )
    print(f"Enrolled identities: {sorted(enrolled.keys())}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Enroll a face identity into embeddings.pkl via ArcFace HEF."
    )
    parser.add_argument("--name", required=True, help="Identity label")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--photo", type=Path, help="Single photo path")
    src.add_argument("--photos", type=Path, help="Directory of photos for one person")
    parser.add_argument(
        "--hef",
        type=Path,
        default=DEFAULT_HEF,
        help="Path to ArcFace HEF (default: %(default)s)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="Embeddings store (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    photos = _collect_photo_paths(args.photo, args.photos)
    enroll(args.name, photos, args.hef, args.out)


if __name__ == "__main__":
    main()
