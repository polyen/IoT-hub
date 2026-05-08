"""CLI: convert ONNX model to HEF via Hailo DFC (hailo_sdk_client).

Must run on x86_64 Ubuntu with hailo_sdk_client installed.
In --ci mode, prints MLflow artifact paths as JSON to stdout.
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


def har_to_hef(har_path: pathlib.Path, model_name: str, out_dir: pathlib.Path) -> pathlib.Path:
    _require_hailo()
    runner: Any = ClientRunner(hw_arch="hailo8", har=str(har_path))
    hef_path = out_dir / f"{model_name}.hef"
    runner.compile()
    runner.save_hef(str(hef_path))
    return hef_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Convert ONNX to HEF via Hailo DFC")
    parser.add_argument("--onnx", required=True, type=pathlib.Path, help="Path to .onnx file")
    parser.add_argument("--out", required=True, type=pathlib.Path, help="Output directory")
    parser.add_argument("--model-name", required=True, help="Model name (used for file naming)")
    parser.add_argument(
        "--ci",
        action="store_true",
        help="CI mode: print artifact paths as JSON to stdout",
    )
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)

    har_path = onnx_to_har(args.onnx, args.model_name, args.out)
    hef_path = har_to_hef(har_path, args.model_name, args.out)

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
