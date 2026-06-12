"""Comparative accuracy benchmark of YOLO detectors (P1.1).

For each ``--config`` entry runs an Ultralytics ``val()`` pass on FP32 weights
(``.onnx`` or ``.pt``) and records **mAP@.5** and **mAP@.5-.95**, overall and
per class (person / fire / smoke), on the mixed test split.

This is a **local FP32 comparison** — it runs on any machine (CPU), no Hailo
required — because the candidate models only co-exist at the FP32 level:
``yolo26n`` is additionally deployed as an INT8 HEF, but ``yolov11n`` was never
compiled past ``.har`` (no HEF), so an on-device INT8 comparison of the two is
not possible. Detector selection is therefore driven by FP32 detection accuracy
here; the deployed 26n's on-Hailo INT8 accuracy (with quantisation loss) is a
separate, device-only measurement.

Throughput/FPS is intentionally not measured: for this event-driven
home-monitoring system inference throughput is not a critical metric (see the
"Performance" note in the generated report).

Outputs a JSON file plus a Markdown table suitable for §18 (Results) of the
thesis.

Usage::

    uv run python -m training.evaluation.cv_detector_compare \\
        --config materials/evaluation_results/cv_detector_compare/config.yaml \\
        --output materials/evaluation_results/cv_detector_compare

Config schema::

    data_yaml: datasets/fire_smoke_mixed/data.yaml
    split: test
    imgsz: 640
    conf: 0.001        # low conf for a full PR curve (Ultralytics val default)
    iou: 0.7           # NMS IoU for val
    classes: [person, fire, smoke]
    models:
      - name: yolo26n-mixed-v1
        weights: models/onnx/yolo26n_mixed_v1.onnx
        notes: "NMS-free; deployed detector"
      - name: yolov11n-mixed-v1
        weights: models/onnx/yolov11n_mixed_v1.onnx
        notes: "accuracy baseline"

On machines without Ultralytics (or with missing weights) ``--dry-run`` emits a
stub Markdown skeleton so the thesis section can be drafted ahead of the run.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import yaml

    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

try:
    from ultralytics import YOLO  # type: ignore[attr-defined]

    ULTRALYTICS_AVAILABLE = True
except ImportError:
    ULTRALYTICS_AVAILABLE = False


DEFAULT_CONF = 0.001
DEFAULT_IOU = 0.7
DEFAULT_IMGSZ = 640
DEFAULT_SPLIT = "test"


@dataclass
class ModelSpec:
    name: str
    weights: Path
    notes: str = ""


@dataclass
class CompareConfig:
    models: list[ModelSpec]
    classes: list[str]
    data_yaml: Path
    split: str = DEFAULT_SPLIT
    imgsz: int = DEFAULT_IMGSZ
    conf: float = DEFAULT_CONF
    iou: float = DEFAULT_IOU


@dataclass
class ModelResult:
    name: str
    weights: str
    notes: str
    map50_overall: float = 0.0
    map5095_overall: float = 0.0
    map50_per_class: dict[str, float] = field(default_factory=dict)
    map5095_per_class: dict[str, float] = field(default_factory=dict)
    error: str = ""


def _load_config(path: Path) -> CompareConfig:
    if not YAML_AVAILABLE:
        raise RuntimeError("PyYAML required: uv pip install pyyaml")
    raw = yaml.safe_load(path.read_text())
    models = [
        ModelSpec(name=m["name"], weights=Path(m["weights"]), notes=m.get("notes", ""))
        for m in raw.get("models", [])
    ]
    return CompareConfig(
        models=models,
        classes=list(raw.get("classes", [])),
        data_yaml=Path(raw["data_yaml"]),
        split=str(raw.get("split", DEFAULT_SPLIT)),
        imgsz=int(raw.get("imgsz", DEFAULT_IMGSZ)),
        conf=float(raw.get("conf", DEFAULT_CONF)),
        iou=float(raw.get("iou", DEFAULT_IOU)),
    )


def _benchmark_one(
    spec: ModelSpec,
    config: CompareConfig,
    output: Path,
    device: str,
) -> ModelResult:
    result = ModelResult(name=spec.name, weights=str(spec.weights), notes=spec.notes)

    if not spec.weights.exists():
        result.error = f"weights not found: {spec.weights}"
        return result
    if not ULTRALYTICS_AVAILABLE:
        result.error = "ultralytics not installed — uv sync --extra training"
        return result

    try:
        model = YOLO(str(spec.weights), task="detect")
        print(f"[{spec.name}] val on {config.data_yaml} (split={config.split}, device={device}) …")
        metrics = model.val(
            data=str(config.data_yaml),
            split=config.split,
            imgsz=config.imgsz,
            conf=config.conf,
            iou=config.iou,
            device=device,
            verbose=False,
            plots=False,
            save_json=False,
            # Absolute path — Ultralytics otherwise prepends its default runs/detect.
            project=str((output / "_val_runs").resolve()),
            name=spec.name,
            exist_ok=True,
        )
    except Exception as exc:  # noqa: BLE001
        result.error = f"val failed: {exc}"
        return result

    box = metrics.box
    result.map50_overall = float(box.map50)
    result.map5095_overall = float(box.map)
    # ``ap_class_index`` lists the class ids that had ground-truth boxes; map each
    # back to its name via the model's own ``names`` mapping.
    names: dict[int, str] = metrics.names
    for i, cls_id in enumerate(box.ap_class_index):
        cls_name = names.get(int(cls_id), str(cls_id))
        result.map50_per_class[cls_name] = float(box.ap50[i])
        result.map5095_per_class[cls_name] = float(box.ap[i])
    return result


def _cell(value: dict[str, float], cls: str) -> str:
    return f"{value[cls]:.3f}" if cls in value else "—"


def _format_markdown(config: CompareConfig, results: list[ModelResult]) -> str:
    cls_h50 = " | ".join(f"mAP@.5 {c}" for c in config.classes)
    lines: list[str] = [
        "# CV Detector Comparative Benchmark — FP32 accuracy",
        "",
        f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"**Dataset:** `{config.data_yaml}` (split `{config.split}`), imgsz {config.imgsz}, "
        f"conf {config.conf}, IoU {config.iou}",
        "",
        "## Headline metrics — detection accuracy",
        "",
        f"| Model | mAP@.5 | mAP@.5-.95 | {cls_h50} |",
        "|---|---|---|" + "---|" * len(config.classes),
    ]
    for r in results:
        if r.error:
            lines.append(
                f"| **{r.name}** | _error: {r.error}_ |" + " — |" * (len(config.classes) + 1)
            )
            continue
        per_class = " | ".join(_cell(r.map50_per_class, c) for c in config.classes)
        lines.append(
            f"| **{r.name}** | {r.map50_overall:.3f} | {r.map5095_overall:.3f} | {per_class} |"
        )

    lines.extend(
        ["", "## Per-class mAP@.5-.95", "", f"| Model | {cls_h50.replace('@.5', '@.5-.95')} |"]
    )
    lines.append("|---|" + "---|" * len(config.classes))
    for r in results:
        if r.error:
            continue
        per_class = " | ".join(_cell(r.map5095_per_class, c) for c in config.classes)
        lines.append(f"| **{r.name}** | {per_class} |")

    lines.extend(
        [
            "",
            "## Notes",
            "",
            *(f"- **{r.name}** (`{r.weights}`): {r.notes or '—'}" for r in results),
            "",
            "## Scope — FP32, not on-device INT8",
            "",
            "- Numbers above are **FP32** accuracy of the exported weights, measured locally "
            "(CPU) with Ultralytics `val()`. Both candidate architectures co-exist only at this "
            "level: `yolo26n` is additionally compiled to an INT8 HEF and deployed, but "
            "`yolov11n` was never compiled past `.har` (no HEF), so an on-device INT8 comparison "
            "of the two is not possible. Detector selection is therefore driven by FP32 accuracy.",
            "- The deployed `yolo26n` INT8 HEF has its own (slightly lower) on-Hailo mAP from "
            "quantisation; that is a separate device-only measurement on the RPi 5.",
            "",
            "## Performance (throughput / FPS)",
            "",
            "- Inference throughput (FPS) is **not** a critical metric for this system and is "
            "therefore not benchmarked. The hub is event-driven home monitoring on a dedicated "
            "Hailo-8 NPU: detections are consumed asynchronously by sensor fusion, with no hard "
            "per-frame deadline, so detector selection is governed by detection accuracy rather "
            "than raw frame rate.",
            "",
            "## Methodology",
            "",
            "- mAP computed by Ultralytics `val()` (COCO-style) on the same data.yaml split, "
            "imgsz, conf and IoU for every model.",
        ]
    )
    return "\n".join(lines) + "\n"


def _dry_run_markdown(config: CompareConfig) -> str:
    """Emit a stub markdown for thesis drafting without running val."""
    stub_results = [
        ModelResult(
            name=m.name, weights=str(m.weights), notes=m.notes, error="dry-run (not evaluated)"
        )
        for m in config.models
    ]
    return _format_markdown(config, stub_results)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("materials/evaluation_results/cv_detector_compare"),
    )
    parser.add_argument("--data-yaml", type=Path, default=None, help="Override config data_yaml")
    parser.add_argument("--split", type=str, default=None, help="Override config split")
    parser.add_argument("--device", type=str, default="cpu", help="cpu | 0 | cuda:0 …")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Emit stub markdown without running val (works without weights/ultralytics).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    config = _load_config(args.config)
    if args.data_yaml is not None:
        config.data_yaml = args.data_yaml
    if args.split is not None:
        config.split = args.split
    args.output.mkdir(parents=True, exist_ok=True)

    md_path = args.output / "cv_detector_compare.md"
    json_path = args.output / "cv_detector_compare.json"

    if args.dry_run or not ULTRALYTICS_AVAILABLE:
        if not ULTRALYTICS_AVAILABLE and not args.dry_run:
            logger.warning("ultralytics absent — emitting dry-run markdown stub")
        md_path.write_text(_dry_run_markdown(config))
        print(f"[dry-run] wrote {md_path}")
        return

    if not config.data_yaml.exists():
        raise SystemExit(f"data_yaml not found: {config.data_yaml}")

    results: list[ModelResult] = []
    for spec in config.models:
        print(f"\n=== Benchmarking {spec.name} ===")
        result = _benchmark_one(spec, config, args.output, args.device)
        results.append(result)
        print(
            f"  → mAP@.5 {result.map50_overall:.3f}, mAP@.5-.95 {result.map5095_overall:.3f}"
            + (f", error: {result.error}" if result.error else "")
        )

    json_path.write_text(
        json.dumps(
            {
                "config": {
                    "classes": config.classes,
                    "data_yaml": str(config.data_yaml),
                    "split": config.split,
                    "imgsz": config.imgsz,
                    "conf": config.conf,
                    "iou": config.iou,
                    "precision": "fp32",
                    "device": args.device,
                },
                "results": [r.__dict__ for r in results],
            },
            indent=2,
        )
    )
    md_path.write_text(_format_markdown(config, results))
    print(f"\nWrote {json_path}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    main()
