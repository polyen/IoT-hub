"""CLI: convert ONNX model to HEF via Hailo DFC (hailo_sdk_client).

Must run on x86_64 Ubuntu with hailo_sdk_client installed.
In --ci mode, prints MLflow artifact paths as JSON to stdout.

YOLO26 note: pass --calib-set to enable quantization calibration (recommended
for NMS-free models — improves int8 accuracy by ~1-2 mAP points).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

try:
    from hailo_sdk_client import ClientRunner  # type: ignore[import]

    HAILO_AVAILABLE = True
except ImportError:
    HAILO_AVAILABLE = False


def _require_hailo() -> None:
    if not HAILO_AVAILABLE:
        raise NotImplementedError(
            "Install hailo_sdk_client on x86_64 Ubuntu — " "see https://hailo.ai/developer-zone/"
        )


def onnx_to_har(onnx_path: pathlib.Path, model_name: str, out_dir: pathlib.Path) -> pathlib.Path:
    _require_hailo()
    runner: Any = ClientRunner(hw_arch="hailo8")
    runner.translate_onnx_model(
        str(onnx_path),
        model_name,
        start_node_names=[],
        end_node_names=[],
        net_input_shapes={},
    )
    har_path = out_dir / f"{model_name}.har"
    runner.save_har(str(har_path))
    return har_path


def har_to_hef(
    har_path: pathlib.Path,
    model_name: str,
    out_dir: pathlib.Path,
    calib_set: pathlib.Path | None = None,
) -> pathlib.Path:
    """Compile HAR to HEF.

    If calib_set is provided, runs quantization calibration first (recommended
    for YOLO26 NMS-free models; pass ~200 representative frames from the test split).
    """
    _require_hailo()
    runner: Any = ClientRunner(hw_arch="hailo8", har=str(har_path))

    if calib_set is not None:
        try:
            import numpy as np  # type: ignore[import]

            images = _load_calib_images(calib_set)
            runner.optimize(np.array(images))
        except Exception as exc:
            print(f"Calibration failed (non-fatal, falling back to default quant): {exc}")

    hef_path = out_dir / f"{model_name}.hef"
    runner.compile()
    runner.save_hef(str(hef_path))
    return hef_path


def _load_calib_images(
    calib_dir: pathlib.Path,
    max_images: int = 200,
    target_size: tuple[int, int] = (640, 640),
) -> list[Any]:
    """Load up to max_images from calib_dir as float32 [H,W,3] arrays."""
    try:
        import cv2  # type: ignore[import]
        import numpy as np  # type: ignore[import]
    except ImportError as e:
        raise ImportError("Install opencv-python and numpy for calibration") from e

    exts = {".jpg", ".jpeg", ".png", ".bmp"}
    paths = sorted(p for p in calib_dir.rglob("*") if p.suffix.lower() in exts)[:max_images]
    if not paths:
        raise FileNotFoundError(f"No images found in {calib_dir}")

    images = []
    for p in paths:
        img = cv2.imread(str(p))
        if img is None:
            continue
        img = cv2.resize(img, target_size)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        images.append(img)
    return images


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Convert ONNX to HEF via Hailo DFC")
    parser.add_argument("--onnx", required=True, type=pathlib.Path, help="Path to .onnx file")
    parser.add_argument("--out", required=True, type=pathlib.Path, help="Output directory")
    parser.add_argument("--model-name", required=True, help="Model name (used for file naming)")
    parser.add_argument(
        "--calib-set",
        type=pathlib.Path,
        default=None,
        help="Directory of calibration images for int8 quantization (YOLO26: ~200 frames)",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="CI mode: print artifact paths as JSON to stdout",
    )
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)

    har_path = onnx_to_har(args.onnx, args.model_name, args.out)
    hef_path = har_to_hef(har_path, args.model_name, args.out, calib_set=args.calib_set)

    if args.ci:
        result = {
            "har": str(har_path),
            "hef": str(hef_path),
            "model_name": args.model_name,
        }
        print(json.dumps(result))
    else:
        print(f"HAR: {har_path}")
        print(f"HEF: {hef_path}")


if __name__ == "__main__":
    main(sys.argv[1:])
