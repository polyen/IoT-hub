"""Fall-detection F1 evaluation on real video clips (e.g. Le2i).

Decodes each clip's frames, runs **pose estimation** to recover COCO keypoints +
person bbox, and feeds them frame-by-frame into the production rule
(``hub.edge.cv.fall_rule.FallDetector``). A clip is predicted *fall* when the
detector emits a ``FallEvent`` at any frame. F1 is computed against the clip
labels.

This measures the *real* rule on *real* pose, not a stub: there is no synthetic
keypoint generator. Pose comes from an Ultralytics YOLO-pose ``.pt`` model on CPU
(portable, reproducible) — the upstream the rule consumes in production.

Honesty contract: if the dataset, clips, ``cv2``, or the pose model are missing,
the result is ``{"measured": false, "F1": null, "pass": null, ...}`` with a
note — never a fabricated F1.

Dataset layout (any of):
  * ``label.json``: ``[{"clip_path": "...", "label": true|false}]``
  * ``fall/`` and ``no_fall/`` subdirectories of clips
  * flat files named ``fall_*`` / ``nofall_*`` / ``normal_*``
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import cv2

    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    from ultralytics import YOLO  # type: ignore[attr-defined]

    ULTRALYTICS_AVAILABLE = True
except ImportError:
    ULTRALYTICS_AVAILABLE = False

_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}


def _not_measured(note: str) -> dict[str, Any]:
    return {"measured": False, "F1": None, "pass": None, "target": 0.80, "note": note}


def load_clips(dataset_dir: Path) -> list[dict[str, Any]]:
    """Discover clips + labels. Returns ``[{"clip_path", "label"}]``."""
    clips: list[dict[str, Any]] = []

    label_json = dataset_dir / "label.json"
    if label_json.exists():
        for entry in json.loads(label_json.read_text()):
            clips.append(
                {"clip_path": dataset_dir / entry["clip_path"], "label": bool(entry["label"])}
            )
        return clips

    fall_dir = dataset_dir / "fall"
    no_fall_dir = dataset_dir / "no_fall"
    if fall_dir.exists() or no_fall_dir.exists():
        for p in sorted(fall_dir.iterdir()) if fall_dir.exists() else []:
            if p.suffix.lower() in _VIDEO_EXTS:
                clips.append({"clip_path": p, "label": True})
        for p in sorted(no_fall_dir.iterdir()) if no_fall_dir.exists() else []:
            if p.suffix.lower() in _VIDEO_EXTS:
                clips.append({"clip_path": p, "label": False})
        return clips

    for p in sorted(dataset_dir.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in _VIDEO_EXTS:
            continue
        name = p.name.lower()
        if "nofall" in name or "no_fall" in name or "normal" in name:
            clips.append({"clip_path": p, "label": False})
        elif "fall" in name:
            clips.append({"clip_path": p, "label": True})
    return clips


class FallEvaluator:
    """Runs pose → FallDetector over clips and scores F1."""

    def __init__(self, pose_model: str, frame_stride: int = 2) -> None:
        self._pose = YOLO(pose_model)
        self._frame_stride = max(1, frame_stride)

    def _predict_clip(self, clip_path: Path) -> bool | None:
        """True/False fall prediction, or None if the clip cannot be read."""
        from hub.edge.cv.fall_rule import FallDetector
        from hub.edge.cv.pose import Keypoints

        cap = cv2.VideoCapture(str(clip_path))
        if not cap.isOpened():
            logger.warning("Cannot open clip: %s", clip_path)
            return None

        detector = FallDetector()
        frame_idx = 0
        fired = False
        read_any = False

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            read_any = True
            if frame_idx % self._frame_stride != 0:
                frame_idx += 1
                continue
            frame_idx += 1

            h, w = frame.shape[:2]
            frame_aspect = (w / h) if h else 1.0
            results = self._pose.predict(frame, verbose=False, device="cpu")
            if not results:
                continue
            r = results[0]
            if r.keypoints is None or r.boxes is None or len(r.boxes) == 0:
                continue

            # Highest-confidence person.
            confs = r.boxes.conf.tolist()
            best = max(range(len(confs)), key=lambda i: confs[i])
            box_n = r.boxes.xyxyn[best].tolist()  # normalised [0,1]
            kpts_xyn = r.keypoints.xyn[best].tolist()  # 17 × [x, y] normalised
            kpt_conf = (
                r.keypoints.conf[best].tolist()
                if r.keypoints.conf is not None
                else [1.0] * len(kpts_xyn)
            )
            points = [
                (float(xy[0]), float(xy[1]), float(c))
                for xy, c in zip(kpts_xyn, kpt_conf, strict=False)
            ]

            event = detector.update(
                track_id=0,
                keypoints=Keypoints(points=points),
                bbox=(box_n[0], box_n[1], box_n[2], box_n[3]),
                frame_aspect=frame_aspect,
            )
            if event is not None:
                fired = True
                break

        cap.release()
        return fired if read_any else None

    def run(self, dataset_dir: Path) -> dict[str, Any]:
        clips = load_clips(dataset_dir)
        if not clips:
            return _not_measured(f"no video clips found in {dataset_dir}")

        tp = fp = tn = fn = 0
        skipped = 0
        for clip in clips:
            pred = self._predict_clip(Path(clip["clip_path"]))
            if pred is None:
                skipped += 1
                continue
            actual = bool(clip["label"])
            if pred and actual:
                tp += 1
            elif pred and not actual:
                fp += 1
            elif not pred and actual:
                fn += 1
            else:
                tn += 1

        scored = tp + fp + tn + fn
        if scored == 0:
            return _not_measured("all clips failed to decode")

        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

        return {
            "measured": True,
            "F1": round(f1, 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "target": 0.80,
            "pass": f1 > 0.80,
            "n_clips": scored,
            "skipped": skipped,
            "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Fall-detection F1 on real video clips")
    parser.add_argument("--dataset", required=True, help="Path to fall dataset root (e.g. Le2i)")
    parser.add_argument(
        "--pose-model",
        default="yolov8n-pose.pt",
        help="Ultralytics YOLO-pose .pt model (auto-downloads if absent)",
    )
    parser.add_argument("--frame-stride", type=int, default=2)
    parser.add_argument("--output", default="materials/evaluation_results")
    args = parser.parse_args()

    dataset_dir = Path(args.dataset)
    if not dataset_dir.exists():
        result = _not_measured(f"dataset not found at {dataset_dir}")
    elif not CV2_AVAILABLE:
        result = _not_measured("opencv-python (cv2) not installed — cannot decode video")
    elif not ULTRALYTICS_AVAILABLE:
        result = _not_measured("ultralytics not installed — cannot run pose estimation")
    else:
        result = FallEvaluator(args.pose_model, frame_stride=args.frame_stride).run(dataset_dir)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cv_fall.json").write_text(json.dumps(result, indent=2))

    logger.info("Results written to %s", out_dir / "cv_fall.json")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
