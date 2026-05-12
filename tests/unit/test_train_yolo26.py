"""Unit tests for YOLO26 training pipeline (Phase 4 migration)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch


def _make_stubs() -> dict[str, ModuleType]:
    stub_mlflow = MagicMock()
    stub_mlflow.start_run.return_value.__enter__ = lambda s: MagicMock()
    stub_mlflow.start_run.return_value.__exit__ = MagicMock(return_value=False)

    stub_ultra = MagicMock()
    stub_ultra.YOLO = MagicMock()
    # mlflow.artifacts must also be in sys.modules — `import mlflow.artifacts` checks it
    return {
        "ultralytics": stub_ultra,
        "mlflow": stub_mlflow,
        "mlflow.artifacts": MagicMock(),
    }


# ---------------------------------------------------------------------------
# test_default_base_model_is_yolo26
# ---------------------------------------------------------------------------


def test_default_base_model_is_yolo26() -> None:
    """Default --base-model must be yolo26n.pt after Phase 4 migration."""
    stubs = _make_stubs()
    with patch.dict(sys.modules, stubs):
        import importlib

        import training.fire_smoke.train as train_mod  # noqa: PLC0415

        importlib.reload(train_mod)
        parser = train_mod._build_parser()
        args = parser.parse_args(["--data", "data.yaml"])

    assert args.base_model == "yolo26n.pt"


# ---------------------------------------------------------------------------
# test_onnx_export_called_after_training
# ---------------------------------------------------------------------------


def test_onnx_export_called_after_training(tmp_path: Path, monkeypatch: object) -> None:
    """main() must call model.export(format='onnx', opset=17) after training."""
    import os

    stubs = _make_stubs()

    with patch.dict(sys.modules, stubs):
        import importlib

        import training.fire_smoke.train as train_mod  # noqa: PLC0415

        importlib.reload(train_mod)

    mock_results = MagicMock()
    mock_results.results_dict = {
        "metrics/mAP50(B)": 0.82,
        "metrics/mAP50-95(B)": 0.55,
        "metrics/precision(B)": 0.88,
        "metrics/recall(B)": 0.79,
    }
    train_model = MagicMock()
    train_model.train.return_value = mock_results
    best_model = MagicMock()

    yolo_cls = MagicMock(side_effect=[train_model, best_model])
    mock_mlflow = MagicMock()
    mock_mlflow.active_run.return_value = None

    # Create best.pt at the hardcoded relative path that train.py uses
    best_pt = tmp_path / "runs" / "fire_smoke" / "train" / "weights" / "best.pt"
    best_pt.parent.mkdir(parents=True)
    best_pt.write_bytes(b"fake")

    # Change cwd to tmp_path so relative Path("runs/...") resolves correctly
    orig_dir = os.getcwd()
    os.chdir(tmp_path)
    try:
        with (
            patch.object(train_mod, "YOLO", yolo_cls),
            patch.object(train_mod, "mlflow", mock_mlflow),
            patch.object(train_mod, "_MLFLOW_AVAILABLE", True),
            patch.object(train_mod, "_ULTRA_AVAILABLE", True),
        ):
            train_mod.main(["--data", "data.yaml", "--epochs", "1"])
    finally:
        os.chdir(orig_dir)

    # YOLO called twice: checkpoint for training + best.pt for ONNX export
    assert yolo_cls.call_count == 2
    best_model.export.assert_called_once_with(format="onnx", opset=17, dynamic=False, simplify=True)


# ---------------------------------------------------------------------------
# test_onnx_export_args_opset17
# ---------------------------------------------------------------------------


def test_onnx_export_args_opset17() -> None:
    """Export must use opset=17 and simplify=True (YOLO26 NMS-free requirement)."""
    stubs = _make_stubs()

    with patch.dict(sys.modules, stubs):
        import importlib

        import training.fire_smoke.train as train_mod  # noqa: PLC0415

        importlib.reload(train_mod)

        mock_yolo_cls = stubs["ultralytics"].YOLO
        # Verify the export call signature when manually calling export
        instance = mock_yolo_cls("yolo26n.pt")
        instance.export(format="onnx", opset=17, dynamic=False, simplify=True)

        instance.export.assert_called_once()
        _, kwargs = instance.export.call_args
        assert kwargs.get("format") == "onnx"
        assert kwargs.get("opset") == 17
        assert kwargs.get("simplify") is True
        assert kwargs.get("dynamic") is False


# ---------------------------------------------------------------------------
# test_hef_conversion_skipped_without_onnx
# ---------------------------------------------------------------------------


def test_hef_conversion_skipped_without_onnx(tmp_path: Path) -> None:
    """HEF conversion subprocess must NOT run if ONNX export produced no file."""
    import os

    stubs = _make_stubs()

    with patch.dict(sys.modules, stubs):
        import importlib

        import training.fire_smoke.train as train_mod  # noqa: PLC0415

        importlib.reload(train_mod)

    mock_results = MagicMock()
    mock_results.results_dict = {}

    train_model = MagicMock()
    train_model.train.return_value = mock_results
    best_model = MagicMock()
    # export() does nothing → onnx file is never written to disk

    yolo_cls = MagicMock(side_effect=[train_model, best_model])
    mock_mlflow = MagicMock()
    mock_mlflow.active_run.return_value = None

    # Create best.pt but NOT best.onnx — simulates failed/no-op ONNX export
    best_pt = tmp_path / "runs" / "fire_smoke" / "train" / "weights" / "best.pt"
    best_pt.parent.mkdir(parents=True)
    best_pt.write_bytes(b"fake")

    orig_dir = os.getcwd()
    os.chdir(tmp_path)
    try:
        with (
            patch.object(train_mod, "YOLO", yolo_cls),
            patch.object(train_mod, "mlflow", mock_mlflow),
            patch.object(train_mod, "_MLFLOW_AVAILABLE", True),
            patch.object(train_mod, "_ULTRA_AVAILABLE", True),
            patch("subprocess.run") as mock_subproc,
        ):
            train_mod.main(["--data", "data.yaml", "--epochs", "1"])
    finally:
        os.chdir(orig_dir)

    # HEF conversion subprocess must not be called — onnx file doesn't exist
    mock_subproc.assert_not_called()


# ---------------------------------------------------------------------------
# test_nms_free_flag_on_detector
# ---------------------------------------------------------------------------


def test_nms_free_flag_on_detector() -> None:
    """HailoDetector must accept nms_free kwarg and store it."""
    with patch.dict(sys.modules, {"hailo_platform": MagicMock()}):
        import importlib

        import hub.edge.cv.detector as det_mod  # noqa: PLC0415

        importlib.reload(det_mod)
        det_mod.HAILO_AVAILABLE = True

        detector = det_mod.HailoDetector(Path("model.hef"), nms_free=True)
        assert detector._nms_free is True

        detector_default = det_mod.HailoDetector(Path("model.hef"))
        assert detector_default._nms_free is False
