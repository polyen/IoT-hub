"""Export trained SetFit model to ONNX FP32, then INT8-quantize for edge.

Output:
    models/intent_classifier/model.onnx        (FP32)
    models/intent_classifier/model.int8.onnx   (INT8 dynamic quantization)
    models/intent_classifier/tokenizer/        (HF tokenizer artifacts)
    models/intent_classifier/classifier_head.npz (sklearn LR weights for ONNX-side dot product)

The edge runtime (hub/edge/agent/intent_classifier.py) loads ``model.int8.onnx``
plus the head weights — that gives sub-100ms inference on Pi5 CPU.

Usage:
    uv run python -m training.intent_classifier.convert_to_onnx \
        --checkpoint models/intent_classifier/checkpoint \
        --out-dir models/intent_classifier
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

from training.intent_classifier.intents import INTENT_LABELS

logger = logging.getLogger(__name__)


def export(checkpoint: Path, out_dir: Path) -> dict[str, Any]:
    """Export sentence-transformer body to ONNX + dump LR head weights as .npz."""
    try:
        from optimum.onnxruntime import (  # type: ignore[import]
            ORTModelForFeatureExtraction,
            ORTQuantizer,  # type: ignore[import]
        )
        from optimum.onnxruntime.configuration import (  # type: ignore[import]
            AutoQuantizationConfig,
        )
        from setfit import SetFitModel  # type: ignore[import]
        from transformers import AutoTokenizer  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "optimum[onnxruntime] + setfit + transformers required. "
            "Run: uv sync --extra training"
        ) from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    fp32_path = out_dir / "model.onnx"
    int8_path = out_dir / "model.int8.onnx"
    tokenizer_dir = out_dir / "tokenizer"
    head_path = out_dir / "classifier_head.npz"

    logger.info("Loading SetFit checkpoint from %s", checkpoint)
    sf_model = SetFitModel.from_pretrained(str(checkpoint))

    # SetFit body is a SentenceTransformer wrapping an underlying HF model.
    # Use optimum to export it via ORTModelForFeatureExtraction (mean-pooled).
    body_path = checkpoint / "0_Transformer"
    if not body_path.exists():
        # Some SetFit versions save under a flat structure — try the checkpoint root
        body_path = checkpoint

    logger.info("Exporting sentence-transformer body to ONNX FP32 → %s", fp32_path)
    ort_model = ORTModelForFeatureExtraction.from_pretrained(str(body_path), export=True)
    ort_model.save_pretrained(str(out_dir))
    # save_pretrained writes model.onnx + config; rename if needed
    auto_path = out_dir / "model.onnx"
    if not auto_path.exists():
        # optimum>=1.20 may name it differently; find the .onnx
        onnx_files = list(out_dir.glob("*.onnx"))
        if len(onnx_files) == 1:
            onnx_files[0].rename(fp32_path)

    # Save tokenizer
    tokenizer = AutoTokenizer.from_pretrained(str(body_path))
    tokenizer.save_pretrained(str(tokenizer_dir))

    # INT8 dynamic quantization (no calibration dataset needed for embeddings)
    logger.info("INT8-quantizing → %s", int8_path)
    qconfig = AutoQuantizationConfig.arm64(is_static=False, per_channel=False)
    quantizer = ORTQuantizer.from_pretrained(str(out_dir), file_name=fp32_path.name)
    quantizer.quantize(save_dir=str(out_dir), quantization_config=qconfig)
    # Quantized file usually named model_quantized.onnx — rename
    quantized = out_dir / "model_quantized.onnx"
    if quantized.exists():
        quantized.rename(int8_path)

    # Extract sklearn LogisticRegression head: (coef, intercept, classes)
    head = sf_model.model_head
    coef = np.asarray(head.coef_, dtype=np.float32)
    intercept = np.asarray(head.intercept_, dtype=np.float32)
    classes = np.asarray(head.classes_)
    # SetFit stores labels as strings; map to canonical INTENT_LABELS indices
    label_strings: list[str] = [str(c) for c in classes.tolist()]
    np.savez(head_path, coef=coef, intercept=intercept)
    (out_dir / "classifier_head_labels.json").write_text(
        json.dumps(label_strings, ensure_ascii=False, indent=2)
    )

    sizes = {
        "fp32_mb": fp32_path.stat().st_size / 1024 / 1024 if fp32_path.exists() else 0,
        "int8_mb": int8_path.stat().st_size / 1024 / 1024 if int8_path.exists() else 0,
    }
    logger.info("Export done: fp32=%.1f MB, int8=%.1f MB", sizes["fp32_mb"], sizes["int8_mb"])
    return {"sizes": sizes, "labels": label_strings, "canonical": INTENT_LABELS}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checkpoint", type=Path, default=Path("models/intent_classifier/checkpoint")
    )
    parser.add_argument("--out-dir", type=Path, default=Path("models/intent_classifier"))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    export(args.checkpoint, args.out_dir)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
