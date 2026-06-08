"""D-Fire mAP@.5 evaluation runner."""

from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from ultralytics import YOLO  # type: ignore[attr-defined]

    ULTRALYTICS_AVAILABLE = True
except ImportError:
    ULTRALYTICS_AVAILABLE = False


@dataclass
class EvalConfig:
    model_path: str
    dataset_dir: str
    conf_threshold: float = 0.25
    iou_threshold: float = 0.5
    device: str = "cpu"


def compute_iou(box1: list[float], box2: list[float]) -> float:
    """Compute IoU between two [x1, y1, x2, y2] boxes."""
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])

    inter_w = max(0.0, x2 - x1)
    inter_h = max(0.0, y2 - y1)
    inter_area = inter_w * inter_h

    area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
    area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
    union_area = area1 + area2 - inter_area

    if union_area <= 0:
        return 0.0
    return inter_area / union_area


def _yolo_txt_to_xyxy(parts: list[str], img_w: float = 1.0, img_h: float = 1.0) -> list[float]:
    """Convert YOLO normalised cx,cy,w,h to x1,y1,x2,y2."""
    cx, cy, w, h = float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
    x1 = (cx - w / 2) * img_w
    y1 = (cy - h / 2) * img_h
    x2 = (cx + w / 2) * img_w
    y2 = (cy + h / 2) * img_h
    return [x1, y1, x2, y2]


class FireSmokeEvaluator:
    """Evaluates fire/smoke detection model on D-Fire dataset."""

    def __init__(self, config: EvalConfig) -> None:
        self.config = config

    def load_ground_truth(self, labels_dir: Path) -> dict[str, list[dict[str, Any]]]:
        """Read YOLO .txt label files → {filename: [{class_id, bbox}]}."""
        gt: dict[str, list[dict[str, Any]]] = defaultdict(list)
        if not labels_dir.exists():
            logger.warning("Labels dir not found: %s", labels_dir)
            return dict(gt)

        for txt_file in sorted(labels_dir.glob("*.txt")):
            stem = txt_file.stem
            for line in txt_file.read_text().strip().splitlines():
                parts = line.strip().split()
                if len(parts) < 5:
                    continue
                class_id = int(parts[0])
                bbox = _yolo_txt_to_xyxy(parts)
                gt[stem].append({"class_id": class_id, "bbox": bbox})
        return dict(gt)

    def run_inference(self, images_dir: Path) -> dict[str, list[dict[str, Any]]]:
        """Run model inference on images_dir. Requires ultralytics + a model path."""
        if not ULTRALYTICS_AVAILABLE:
            raise RuntimeError("ultralytics not installed — cannot run real inference")
        if not self.config.model_path:
            raise RuntimeError("--model is required (path to a .pt/.onnx detector)")

        model = YOLO(self.config.model_path)
        predictions: dict[str, list[dict[str, Any]]] = {}

        image_files = (
            list(images_dir.glob("*.jpg"))
            + list(images_dir.glob("*.jpeg"))
            + list(images_dir.glob("*.png"))
        )

        for img_path in sorted(image_files):
            results = model.predict(
                str(img_path),
                conf=self.config.conf_threshold,
                device=self.config.device,
                verbose=False,
            )
            preds: list[dict[str, Any]] = []
            for r in results:
                if r.boxes is None:
                    continue
                for box in r.boxes:  # type: ignore[attr-defined]
                    xyxy = box.xyxy[0].tolist()
                    preds.append(
                        {
                            "class_id": int(box.cls[0]),
                            "confidence": float(box.conf[0]),
                            "bbox": xyxy,
                        }
                    )
            predictions[img_path.stem] = preds

        return predictions

    def compute_map(
        self,
        predictions: dict[str, list[dict[str, Any]]],
        ground_truth: dict[str, list[dict[str, Any]]],
        iou_thresh: float = 0.5,
    ) -> float:
        """Simplified mAP@iou_thresh: per-class AP averaged across classes."""
        if not ground_truth:
            return 0.0

        # Collect all class ids
        all_classes: set[int] = set()
        for boxes in ground_truth.values():
            for b in boxes:
                all_classes.add(b["class_id"])

        aps: list[float] = []
        for cls_id in sorted(all_classes):
            # Build sorted list of (confidence, tp) across all images
            entries: list[tuple[float, int]] = []
            n_gt = 0

            for stem, gt_boxes in ground_truth.items():
                gt_cls = [b for b in gt_boxes if b["class_id"] == cls_id]
                pred_cls = [p for p in predictions.get(stem, []) if p["class_id"] == cls_id]
                n_gt += len(gt_cls)

                matched_gt: set[int] = set()
                for pred in sorted(pred_cls, key=lambda x: -x.get("confidence", 1.0)):
                    best_iou = 0.0
                    best_j = -1
                    for j, gt in enumerate(gt_cls):
                        iou = compute_iou(pred["bbox"], gt["bbox"])
                        if iou > best_iou:
                            best_iou = iou
                            best_j = j
                    if best_iou >= iou_thresh and best_j not in matched_gt:
                        matched_gt.add(best_j)
                        entries.append((pred.get("confidence", 1.0), 1))
                    else:
                        entries.append((pred.get("confidence", 1.0), 0))

            if n_gt == 0:
                continue

            # Sort by descending confidence
            entries.sort(key=lambda x: -x[0])

            # Precision-recall curve
            tp_cum = 0
            fp_cum = 0
            precisions: list[float] = []
            recalls: list[float] = []
            for _conf, tp in entries:
                if tp:
                    tp_cum += 1
                else:
                    fp_cum += 1
                precisions.append(tp_cum / (tp_cum + fp_cum))
                recalls.append(tp_cum / n_gt)

            # Interpolated AP (11-point)
            ap = 0.0
            for t in [r / 10.0 for r in range(11)]:
                p_at_t = max(
                    (p for p, r in zip(precisions, recalls, strict=False) if r >= t),
                    default=0.0,
                )
                ap += p_at_t / 11.0
            aps.append(ap)

        return float(sum(aps) / len(aps)) if aps else 0.0

    def evaluate(self, dataset_dir: Path) -> dict[str, Any]:
        """Run full evaluation pipeline and return metrics dict.

        Returns ``measured: false`` (no fabricated mAP) when ultralytics or a
        model is missing, or when there are no labels to score against.
        """
        images_dir = dataset_dir / "images"
        labels_dir = dataset_dir / "labels"

        if not images_dir.exists():
            images_dir = dataset_dir

        try:
            predictions = self.run_inference(images_dir)
        except RuntimeError as exc:
            return {"measured": False, "mAP50": None, "pass": None, "note": str(exc)}

        ground_truth = self.load_ground_truth(labels_dir)
        if not ground_truth:
            return {
                "measured": False,
                "mAP50": None,
                "pass": None,
                "note": f"no labels found in {labels_dir}",
            }

        n_images = len(predictions)
        map50 = self.compute_map(predictions, ground_truth, iou_thresh=self.config.iou_threshold)

        return {
            "measured": True,
            "mAP50": round(map50, 4),
            "baseline": 0.32,
            "target": 0.78,
            "pass": map50 > 0.78,
            "n_images": n_images,
        }


def evaluate_with_val(
    model_path: str,
    data_yaml: str,
    *,
    split: str = "test",
    conf: float = 0.001,
    iou: float = 0.7,
    device: str = "cpu",
    output_dir: Path,
) -> dict[str, Any]:
    """COCO-style evaluation via Ultralytics ``YOLO.val()``.

    Produces **mAP@.5 *and* mAP@.5-.95** plus per-class AP and precision/recall —
    the full set §4.3.3 of the thesis needs, computed by the canonical metric
    implementation rather than our simplified greedy mAP@.5. Runs the ``.pt``
    model on CPU/MPS (model-accuracy figure; on-NPU FPS comes from
    ``cv_detector_compare``).

    ``conf`` defaults to ``0.001`` and ``iou`` (NMS) to ``0.7`` — the standard
    validation settings published mAP numbers are computed with. A high ``conf``
    truncates the precision-recall curve and *under-reports* mAP, so do not raise
    it here unless you specifically want operating-point precision/recall.
    """
    if not ULTRALYTICS_AVAILABLE:
        return {"measured": False, "mAP50": None, "pass": None, "note": "ultralytics not installed"}
    if not model_path:
        return {"measured": False, "mAP50": None, "pass": None, "note": "--model is required"}
    if not Path(data_yaml).exists():
        return {
            "measured": False,
            "mAP50": None,
            "pass": None,
            "note": f"data yaml not found: {data_yaml}",
        }

    model = YOLO(model_path)
    metrics = model.val(
        data=data_yaml,
        split=split,
        conf=conf,
        iou=iou,
        device=device,
        plots=False,
        verbose=False,
        project=str(output_dir / "_ultralytics_val"),
        name=Path(model_path).stem,
        exist_ok=True,
    )

    names = getattr(metrics, "names", None) or model.names
    per_class: dict[str, dict[str, float]] = {}
    for i, cls_id in enumerate(metrics.box.ap_class_index):
        cls_name = names.get(int(cls_id), str(cls_id)) if isinstance(names, dict) else str(cls_id)
        per_class[cls_name] = {
            "mAP50": round(float(metrics.box.ap50[i]), 4),
            "mAP50_95": round(float(metrics.box.ap[i]), 4),
        }

    map50 = round(float(metrics.box.map50), 4)
    return {
        "measured": True,
        "method": "ultralytics_val",
        "model": Path(model_path).name,
        "split": split,
        "mAP50": map50,
        "mAP50_95": round(float(metrics.box.map), 4),
        "precision": round(float(metrics.box.mp), 4),
        "recall": round(float(metrics.box.mr), 4),
        "per_class": per_class,
        "baseline": 0.32,
        "target": 0.78,
        "pass": map50 > 0.78,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="D-Fire fire/smoke detection mAP evaluation")
    parser.add_argument("--model", default="", help="Path to model (.pt or .hef)")
    parser.add_argument("--dataset", required=True, help="Dataset root dir")
    parser.add_argument(
        "--data-yaml",
        default="",
        help="Ultralytics data.yaml — enables COCO-style mAP@.5-.95 via YOLO.val()",
    )
    parser.add_argument("--split", default="test", help="Dataset split for --data-yaml val()")
    parser.add_argument("--output", default="materials/evaluation_results", help="Output dir")
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.5)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.data_yaml:
        # val() uses its own standard mAP settings (conf=0.001, iou=0.7); the
        # greedy-path --conf/--iou (operating-point thresholds) are not forwarded
        # here, since a high conf would truncate the PR curve and under-report mAP.
        result = evaluate_with_val(
            args.model,
            args.data_yaml,
            split=args.split,
            device=args.device,
            output_dir=out_dir,
        )
    else:
        config = EvalConfig(
            model_path=args.model,
            dataset_dir=args.dataset,
            conf_threshold=args.conf,
            iou_threshold=args.iou,
            device=args.device,
        )
        result = FireSmokeEvaluator(config).evaluate(Path(args.dataset))

    out_file = out_dir / "cv_fire_smoke.json"
    with open(out_file, "w") as f:
        json.dump(result, f, indent=2)

    logger.info("Results written to %s", out_file)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
