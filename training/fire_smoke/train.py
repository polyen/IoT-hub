"""Fine-tune YOLO26n on fire/smoke dataset with MLflow tracking.

Default base model is ``yolo26n.pt`` (see params.yaml). YOLO26 exports NMS-free
so downstream ``convert_to_hef.py`` should be called with ``--calib-set`` and
the Hailo detector loaded with ``nms_free=True``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

try:
    import mlflow
    import mlflow.artifacts

    _MLFLOW_AVAILABLE = True
except ImportError:
    mlflow = None  # noqa: F841
    _MLFLOW_AVAILABLE = False

try:
    from ultralytics import YOLO

    _ULTRA_AVAILABLE = True
except ImportError:
    YOLO = None  # noqa: F841
    _ULTRA_AVAILABLE = False


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fine-tune YOLO26n on fire/smoke dataset with MLflow tracking."
    )
    parser.add_argument(
        "--data",
        required=True,
        type=Path,
        help="Path to YOLO data.yaml",
    )
    parser.add_argument(
        "--base-model",
        default="yolo26n.pt",
        help="Path to .pt checkpoint or 'yolo26n.pt' to download",
    )
    parser.add_argument("--epochs", type=int, default=50, help="Training epochs")
    parser.add_argument("--imgsz", type=int, default=640, help="Image size")
    parser.add_argument("--batch", type=int, default=16, help="Batch size")
    parser.add_argument(
        "--lr",
        type=float,
        default=0.001,
        help="Initial learning rate (lower than YOLOv default for fine-tuning)",
    )
    parser.add_argument("--device", default="cpu", help="Training device (cpu / 0 / 0,1 ...)")
    parser.add_argument(
        "--mlflow-uri",
        default="http://localhost:5001",
        help="MLflow tracking server URI",
    )
    parser.add_argument(
        "--experiment",
        default="fire_smoke_finetune",
        help="MLflow experiment name",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from last checkpoint (runs/fire_smoke/train/weights/last.pt)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    if YOLO is None:
        print(
            "Error: 'ultralytics' is not installed.\n" "Install it with:  pip install ultralytics",
            file=sys.stderr,
        )
        sys.exit(1)

    if mlflow is None:
        print(
            "Error: 'mlflow' is not installed.\n" "Install it with:  pip install mlflow",
            file=sys.stderr,
        )
        sys.exit(1)

    parser = _build_parser()
    args = parser.parse_args(argv)

    mlflow.set_tracking_uri(args.mlflow_uri)
    mlflow.set_experiment(args.experiment)

    with mlflow.start_run():
        mlflow.log_params(
            {
                "epochs": args.epochs,
                "imgsz": args.imgsz,
                "batch": args.batch,
                "lr": args.lr,
                "base_model": args.base_model,
                "data": str(args.data),
            }
        )

        # Use absolute project path so YOLO doesn't prepend its default
        # "runs/detect/" base directory to the project name.
        project_dir = Path("runs/fire_smoke").resolve()
        run_dir = project_dir / "train"

        last_pt = run_dir / "weights" / "last.pt"
        resuming = args.resume and last_pt.exists()

        if resuming:
            # YOLO resume=True reads the full training state (epoch, optimizer,
            # scheduler) from last.pt — all other train() kwargs are ignored.
            print(f"Resuming from {last_pt}")
            model = YOLO(str(last_pt))
            results = model.train(resume=True)
        else:
            model = YOLO(args.base_model)
            results = model.train(
                data=str(args.data),
                epochs=args.epochs,
                imgsz=args.imgsz,
                batch=args.batch,
                lr0=args.lr,
                device=args.device,
                project=str(project_dir),
                name="train",
                exist_ok=True,
            )

        # Log metrics from results dict
        metrics: dict[str, float] = {
            "mAP50": float(results.results_dict.get("metrics/mAP50(B)", 0)),
            "mAP50-95": float(results.results_dict.get("metrics/mAP50-95(B)", 0)),
            "precision": float(results.results_dict.get("metrics/precision(B)", 0)),
            "recall": float(results.results_dict.get("metrics/recall(B)", 0)),
        }
        mlflow.log_metrics(metrics)

        # Log best.pt as MLflow artifact
        best_pt_path = run_dir / "weights" / "best.pt"
        if best_pt_path.exists():
            mlflow.log_artifact(str(best_pt_path), artifact_path="model")

            # Export best.pt → ONNX (opset 17, NMS-free for YOLO26)
            onnx_path = best_pt_path.with_suffix(".onnx")
            try:
                best_model = YOLO(str(best_pt_path))
                best_model.export(format="onnx", opset=17, dynamic=False, simplify=True)
                if onnx_path.exists():
                    mlflow.log_artifact(str(onnx_path), artifact_path="model")
                    print(f"ONNX logged: {onnx_path}")
            except Exception as exc:  # noqa: BLE001
                print(f"ONNX export error (non-fatal): {exc}")

            # Optionally convert to HEF if convert_to_hef.py is present
            convert_script = Path(__file__).parents[1] / "convert_to_hef.py"
            if convert_script.exists() and onnx_path.exists():
                hef_path = best_pt_path.with_suffix(".hef")
                try:
                    proc = subprocess.run(
                        [
                            sys.executable,
                            str(convert_script),
                            "--onnx",
                            str(onnx_path),
                            "--out",
                            str(hef_path.parent),
                            "--model-name",
                            hef_path.stem,
                            "--ci",
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if proc.returncode == 0 and hef_path.exists():
                        mlflow.log_artifact(str(hef_path), artifact_path="model")
                        print(f"HEF logged: {hef_path}")
                    else:
                        print(f"HEF conversion skipped or failed: {proc.stderr.strip()}")
                except Exception as exc:  # noqa: BLE001
                    print(f"HEF conversion error (non-fatal): {exc}")

        run = mlflow.active_run()
        if run is not None:
            print(f"MLflow run ID : {run.info.run_id}")
            print(f"Artifact URI  : {run.info.artifact_uri}")
        print("Metrics:", metrics)


if __name__ == "__main__":
    main()
