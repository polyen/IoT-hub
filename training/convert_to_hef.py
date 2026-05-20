"""CLI: convert ONNX model to HEF via Hailo DFC (hailo_sdk_client).

Must run on x86_64 Ubuntu with hailo_sdk_client installed.
In --ci mode, prints MLflow artifact paths as JSON to stdout.

YOLO26 note: Hailo hardware does not support the NMS-free head's top-k ops
(GatherElements, TopK, ReduceMax). The DFC recommends end_node_names that cut
the graph before those ops, and this script auto-retries with them — BUT the
recommendation cuts at the concatenated head /model.23/Concat_3 (1, 7, 8400).
That single tensor mixes box coords (~0-640) and class scores (0-1) under one
uint8 quantisation scale, which collapses every class score to zero — the
detector then never fires. For YOLO26 you MUST pass --end-nodes explicitly so
box and class become SEPARATE outputs, each with its own scale:

    --end-nodes /model.23/Mul_2 --end-nodes /model.23/Sigmoid

Pass --calib-set to enable int8 quantization calibration (recommended; improves
mAP by ~1-2 pts). Calibration images are normalised to [0,1], so the runtime
(hub.edge.cv.detector) must feed [0,1] input too.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
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


def _read_onnx_input_shapes(onnx_path: pathlib.Path) -> dict[str, list[int]]:
    """Return {input_name: [dim, ...]} from the ONNX graph.

    Dynamic dimensions (dim_value == 0, e.g. dynamic batch) are replaced with 1.
    Falls back to the Ultralytics YOLO default if onnx is not importable.
    """
    try:
        import onnx as _onnx  # type: ignore[import]

        m = _onnx.load(str(onnx_path))
        shapes: dict[str, list[int]] = {}
        for inp in m.graph.input:
            dims = inp.type.tensor_type.shape.dim
            shapes[inp.name] = [max(1, d.dim_value) for d in dims]
        return shapes
    except ImportError:
        return {"images": [1, 3, 640, 640]}


def _extract_recommended_end_nodes(exc: Exception) -> list[str] | None:
    """Parse Hailo DFC's 'please use these end node names: X, Y' recommendation."""
    match = re.search(r"end node names:\s*([^\n]+)", str(exc))
    if not match:
        return None
    return [n.strip() for n in match.group(1).split(",") if n.strip()]


def onnx_to_har(
    onnx_path: pathlib.Path,
    model_name: str,
    out_dir: pathlib.Path,
    input_shapes: dict[str, list[int]] | None = None,
    end_node_names: list[str] | None = None,
) -> pathlib.Path:
    _require_hailo()
    shapes = input_shapes or _read_onnx_input_shapes(onnx_path)
    print(f"[convert] input shapes: {shapes}")

    def _translate(runner: Any, end_nodes: list[str]) -> None:
        if end_nodes:
            print(f"[convert] end nodes: {end_nodes}")
        runner.translate_onnx_model(
            str(onnx_path),
            model_name,
            start_node_names=list(shapes.keys()),
            end_node_names=end_nodes,
            net_input_shapes=shapes,
        )

    runner: Any = ClientRunner(hw_arch="hailo8")
    explicit_ends = end_node_names or []
    try:
        _translate(runner, explicit_ends)
    except Exception as exc:
        # YOLO NMS ops (GatherElements, TopK, …) are unsupported — Hailo DFC
        # recommends cutting the graph before them.
        recommended = _extract_recommended_end_nodes(exc)
        if recommended and not explicit_ends:
            print(
                f"[convert] NMS ops unsupported — retrying with recommended "
                f"end nodes: {recommended}"
            )
            runner = ClientRunner(hw_arch="hailo8")
            _translate(runner, recommended)
        else:
            raise

    har_path = out_dir / f"{model_name}.har"
    runner.save_har(str(har_path))
    return har_path


_CALIB_N = 64  # synthetic images when no real calibration set is given


def har_to_hef(
    har_path: pathlib.Path,
    model_name: str,
    out_dir: pathlib.Path,
    calib_set: pathlib.Path | None = None,
    input_hw: tuple[int, int] = (640, 640),
) -> pathlib.Path:
    """Compile HAR to HEF.

    optimize() (quantization) is mandatory before compile(). If calib_set is
    provided, real images are used (recommended; ~200 frames improves mAP by
    ~1-2 pts). Otherwise, synthetic random images are used so the model still
    compiles, at the cost of slightly lower int8 accuracy.
    """
    _require_hailo()
    import numpy as np  # type: ignore[import]

    runner: Any = ClientRunner(hw_arch="hailo8", har=str(har_path))

    if calib_set is not None:
        try:
            images = _load_calib_images(calib_set, target_size=input_hw)
            print(f"[convert] quantizing with {len(images)} calibration images ...")
            runner.optimize(np.array(images))
        except Exception as exc:
            print(f"[convert] calibration failed ({exc}); falling back to synthetic data")
            calib_set = None

    if calib_set is None:
        print(
            f"[convert] no calibration data — using {_CALIB_N} synthetic random images "
            f"(add datasets/{model_name}/calibration/ for better int8 accuracy)"
        )
        h, w = input_hw
        synthetic = np.random.rand(_CALIB_N, h, w, 3).astype(np.float32)
        runner.optimize(synthetic)

    hef_bytes = runner.compile()
    hef_path = out_dir / f"{model_name}.hef"
    with open(hef_path, "wb") as f:
        f.write(hef_bytes)
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
        "--input-shape",
        metavar="NAME:B,C,H,W",
        action="append",
        default=None,
        help="Override input shape, e.g. --input-shape images:1,3,640,640 (repeatable)",
    )
    parser.add_argument(
        "--end-nodes",
        metavar="NODE",
        action="append",
        default=None,
        help="Explicit end node(s) to cut the graph. REQUIRED for YOLO26: pass "
        "/model.23/Mul_2 and /model.23/Sigmoid as separate outputs — see module "
        "docstring. Auto-detection (when omitted) produces a broken concat output.",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="CI mode: print artifact paths as JSON to stdout",
    )
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)

    input_shapes: dict[str, list[int]] | None = None
    if args.input_shape:
        input_shapes = {}
        for spec in args.input_shape:
            name, dims_str = spec.split(":", 1)
            input_shapes[name] = [int(d) for d in dims_str.split(",")]

    har_path = onnx_to_har(
        args.onnx,
        args.model_name,
        args.out,
        input_shapes=input_shapes,
        end_node_names=args.end_nodes,
    )
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
