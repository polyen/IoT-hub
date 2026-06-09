"""Fine-tune YOLO26n on a YOLO-format dataset with MLflow tracking.

The number of classes is read by Ultralytics from the ``names:`` field of the
data.yaml passed via ``--data``. Default training target is the 3-class mixed
dataset (person + fire + smoke) built by ``training.datasets.prepare_mixed``;
pointing ``--data`` at ``datasets/fire_smoke/data.yaml`` reproduces the legacy
2-class run.

Default base model is ``yolo26n.pt`` (see params.yaml). YOLO26 exports NMS-free
so downstream ``convert_to_hef.py`` should be called with ``--calib-set`` and
the Hailo detector loaded with ``nms_free=True``.
"""

from __future__ import annotations

import argparse
import os
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
    from ultralytics.utils import SETTINGS as _ULTRA_SETTINGS

    _ULTRA_AVAILABLE = True
except ImportError:
    YOLO = None  # noqa: F841
    _ULTRA_SETTINGS = None  # noqa: F841
    _ULTRA_AVAILABLE = False


def _disable_ultralytics_mlflow_callbacks(model: YOLO) -> None:
    """Disable Ultralytics' built-in MLflow integration.

    Mutating ``model.callbacks`` is not enough: ``BaseTrainer.__init__`` rebuilds
    callbacks from ``default_callbacks`` and then calls ``add_integration_callbacks``,
    which re-attaches the MLflow callback when ``SETTINGS["mlflow"]`` is True. The
    integration's ``on_train_end`` hook also tries to ``log_artifact`` to whatever
    artifact URI the server returns, which conflicts with our explicit logging and
    fails outright when the server is configured in proxy-artifact mode but returns
    a server-side filesystem path. Flipping the global setting prevents the import
    in ``add_integration_callbacks`` from running at all.
    """

    if _ULTRA_SETTINGS is not None and _ULTRA_SETTINGS.get("mlflow"):
        _ULTRA_SETTINGS.update({"mlflow": False})
        print("Disabled Ultralytics MLflow integration via SETTINGS")

    removed = 0
    for event, callback_list in model.callbacks.items():
        kept_callbacks = [
            cb
            for cb in callback_list
            if "ultralytics.utils.callbacks.mlflow" not in getattr(cb, "__module__", "")
        ]
        removed += len(callback_list) - len(kept_callbacks)
        model.callbacks[event] = kept_callbacks

    if removed:
        print(f"Removed {removed} pre-attached Ultralytics MLflow callback(s)")


def _register_mlflow_epoch_logging(model: YOLO) -> None:
    """Log metrics to MLflow at the end of every fit epoch.

    The end-of-run ``mlflow.log_metrics`` call only fires if ``model.train()``
    returns normally; an OOM/SIGKILL mid-run (common on ``mps``) loses every
    metric. Logging per-epoch means partial runs still capture progress and the
    run is watchable live in the MLflow UI. We attach our own callback rather
    than re-enabling Ultralytics' integration so we keep full control of the
    active run and avoid its conflicting ``on_train_end`` artifact upload.
    """

    def _on_fit_epoch_end(trainer: object) -> None:
        raw = getattr(trainer, "metrics", None) or {}
        step = int(getattr(trainer, "epoch", 0))
        clean: dict[str, float] = {}
        for key, value in raw.items():
            try:
                clean[key.replace("(B)", "").replace(":", "_")] = float(value)
            except (TypeError, ValueError):
                continue
        if clean:
            try:
                mlflow.log_metrics(clean, step=step)
            except Exception as exc:  # noqa: BLE001
                print(f"MLflow per-epoch logging warning (non-fatal): {exc}")

    def _on_train_epoch_end(_trainer: object) -> None:
        # MPS accumulates memory across epochs on unified-memory Macs, leading
        # to a jetsam SIGKILL (exit 137). Release the cache each epoch.
        try:
            import torch

            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
        except Exception:  # noqa: BLE001
            pass

    model.add_callback("on_fit_epoch_end", _on_fit_epoch_end)
    model.add_callback("on_train_epoch_end", _on_train_epoch_end)


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
        "--name",
        default="train",
        help=(
            "Output sub-directory under runs/fire_smoke/ (default 'train'). "
            "Set a distinct name per architecture (e.g. yolo26n / yolov11n) so "
            "training both does not overwrite the same runs/fire_smoke/train dir."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from last checkpoint (runs/fire_smoke/<name>/weights/last.pt)",
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

    # Keep environment URI aligned for any third-party code that inspects only
    # MLFLOW_TRACKING_URI, while this script still uses explicit API calls.
    os.environ["MLFLOW_TRACKING_URI"] = args.mlflow_uri

    mlflow.set_tracking_uri(args.mlflow_uri)
    mlflow.set_experiment(args.experiment)

    with mlflow.start_run(run_name=args.name):
        mlflow.log_params(
            {
                "epochs": args.epochs,
                "imgsz": args.imgsz,
                "batch": args.batch,
                "lr": args.lr,
                "base_model": args.base_model,
                "data": str(args.data),
                "name": args.name,
            }
        )

        # Use absolute project path so YOLO doesn't prepend its default
        # "runs/detect/" base directory to the project name.
        project_dir = Path("runs/fire_smoke").resolve()
        run_dir = project_dir / args.name

        last_pt = run_dir / "weights" / "last.pt"
        resuming = args.resume and last_pt.exists()

        if resuming:
            # YOLO resume=True reads the full training state (epoch, optimizer,
            # scheduler) from last.pt — all other train() kwargs are ignored.
            print(f"Resuming from {last_pt}")
            model = YOLO(str(last_pt))
            _disable_ultralytics_mlflow_callbacks(model)
            _register_mlflow_epoch_logging(model)
            results = model.train(resume=True)
        else:
            model = YOLO(args.base_model)
            _disable_ultralytics_mlflow_callbacks(model)
            _register_mlflow_epoch_logging(model)
            results = model.train(
                data=str(args.data),
                epochs=args.epochs,
                imgsz=args.imgsz,
                batch=args.batch,
                lr0=args.lr,
                device=args.device,
                project=str(project_dir),
                name=args.name,
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
            try:
                mlflow.log_artifact(str(best_pt_path), artifact_path="model")
            except Exception as exc:  # noqa: BLE001
                print(f"MLflow artifact logging warning for best.pt (non-fatal): {exc}")

            # Export best.pt → ONNX (opset 17, NMS-free for YOLO26)
            onnx_path = best_pt_path.with_suffix(".onnx")
            try:
                best_model = YOLO(str(best_pt_path))
                best_model.export(format="onnx", opset=17, dynamic=False, simplify=True)
                if onnx_path.exists():
                    try:
                        mlflow.log_artifact(str(onnx_path), artifact_path="model")
                    except Exception as exc:  # noqa: BLE001
                        print(f"MLflow artifact logging warning for ONNX (non-fatal): {exc}")
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
                        try:
                            mlflow.log_artifact(str(hef_path), artifact_path="model")
                        except Exception as exc:  # noqa: BLE001
                            print(f"MLflow artifact logging warning for HEF (non-fatal): {exc}")
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
