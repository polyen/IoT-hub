"""Enrollment script: photo -> ArcFace embedding -> embeddings.pkl.

Usage: python -m training.person_reid.enroll --photo face.jpg --name vlad --out models/embeddings.pkl
"""

from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path


def enroll(photo_path: Path, name: str, out_path: Path) -> None:
    """Extract embedding and add/update entry in embeddings.pkl."""
    if out_path.exists():
        with open(out_path, "rb") as f:
            pickle.load(f)  # noqa: S301 — validate file is readable
    raise NotImplementedError(
        "Run on RPi5 with HailoRT + ArcFace HEF model. "
        "First compile ArcFace Buffalo HEF via T0.8 workflow."
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Enroll face embedding")
    parser.add_argument("--photo", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--out", default=Path("models/embeddings.pkl"), type=Path)
    args = parser.parse_args(argv)
    enroll(args.photo, args.name, args.out)


if __name__ == "__main__":
    main(sys.argv[1:])
