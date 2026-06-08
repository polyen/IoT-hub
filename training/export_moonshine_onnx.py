"""Export Moonshine-base-uk (Ukrainian) to ONNX for the edge STT backend.

Ukrainian Moonshine (``UsefulSensors/moonshine-base-uk``) ships **only as
safetensors** — there is no published ONNX. This one-time tool produces the
ONNX the production voice pipeline loads via ``onnxruntime``:

    encoder_model.onnx
    decoder_model_merged.onnx   (KV-cache decoder, single graph)
    tokenizer.json              (the Ukrainian tokenizer)
    + config / preprocessor json

``torch`` + ``optimum`` are needed **only here**, at export time — the runtime
backend (``hub.edge.voice.moonshine_stt.MoonshineUkBackend``) uses plain
``onnxruntime`` + ``tokenizers`` and pulls in neither.

Usage (run once on a dev box / build host, then ship the output dir to the
device's models tree)::

    uv run python -m training.export_moonshine_onnx \
        --output /opt/iot-hub/models/moonshine-base-uk-onnx

The backend reads this path from ``MOONSHINE_ONNX_DIR``.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL_ID = "UsefulSensors/moonshine-base-uk"
TASK = "automatic-speech-recognition-with-past"
# Files the runtime backend actually needs from the export.
REQUIRED_FILES = ("encoder_model.onnx", "decoder_model_merged.onnx", "tokenizer.json")


def _default_output() -> Path:
    models_dir = os.environ.get("MODELS_HOST_DIR", "/opt/iot-hub/models")
    return Path(models_dir) / "moonshine-base-uk-onnx"


def export(output_dir: Path) -> int:
    try:
        from optimum.exporters.onnx import main_export
    except ImportError:
        logger.error(
            "optimum is required for export: pip install 'optimum[onnxruntime]' torch transformers"
        )
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Exporting %s → %s (task=%s) …", MODEL_ID, output_dir, TASK)
    main_export(model_name_or_path=MODEL_ID, output=str(output_dir), task=TASK)

    missing = [f for f in REQUIRED_FILES if not (output_dir / f).exists()]
    if missing:
        logger.error("Export finished but required files are missing: %s", missing)
        return 1

    logger.info("Export OK. Required files present in %s:", output_dir)
    for f in REQUIRED_FILES:
        size_mb = (output_dir / f).stat().st_size / 1e6
        logger.info("  %s (%.1f MB)", f, size_mb)
    logger.info("Point the backend at it with MOONSHINE_ONNX_DIR=%s", output_dir)
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Export Moonshine-base-uk to ONNX for edge STT")
    parser.add_argument(
        "--output",
        type=Path,
        default=_default_output(),
        help="Output dir (default: $MODELS_HOST_DIR/moonshine-base-uk-onnx)",
    )
    args = parser.parse_args()
    sys.exit(export(args.output))


if __name__ == "__main__":
    main()
