"""Unit tests for training.fire_smoke.train."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# test_train_script_importable
# ---------------------------------------------------------------------------


def test_train_script_importable() -> None:
    """training.fire_smoke.train should be importable even without ultralytics."""
    # Stub ultralytics and mlflow if not installed so the import always succeeds
    stubs: dict[str, ModuleType] = {}

    if "ultralytics" not in sys.modules:
        stub_ultra = MagicMock()
        stub_ultra.YOLO = MagicMock()
        stubs["ultralytics"] = stub_ultra

    if "mlflow" not in sys.modules:
        stub_mlflow = MagicMock()
        stubs["mlflow"] = stub_mlflow

    with patch.dict(sys.modules, stubs):
        import importlib

        import training.fire_smoke.train as train_mod  # noqa: PLC0415

        importlib.reload(train_mod)  # reload with stubs in place

    # If we get here, the import succeeded
    assert train_mod is not None


# ---------------------------------------------------------------------------
# test_argparse_defaults
# ---------------------------------------------------------------------------


def test_argparse_defaults() -> None:
    """_build_parser().parse_args([]) should fail for required --data;
    verify all optional defaults are correct by passing --data dummy."""
    stub_ultra = MagicMock()
    stub_mlflow = MagicMock()

    with patch.dict(sys.modules, {"ultralytics": stub_ultra, "mlflow": stub_mlflow}):
        import importlib

        import training.fire_smoke.train as train_mod  # noqa: PLC0415

        importlib.reload(train_mod)

        parser = train_mod._build_parser()
        args = parser.parse_args(["--data", "data.yaml"])

    assert args.epochs == 50
    assert abs(args.lr - 0.001) < 1e-9
    assert args.imgsz == 640
    assert args.batch == 16
    assert args.device == "cpu"
    assert args.mlflow_uri == "http://localhost:5000"
    assert args.experiment == "fire_smoke_finetune"
    assert args.base_model == "yolo11n.pt"
    assert args.resume is False
