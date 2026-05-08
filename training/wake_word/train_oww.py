"""Train custom wake word model for 'хей хата' using openWakeWord trainer.

Usage:
    python -m training.wake_word.train_oww \
        --samples-dir datasets/voice/wake_word/positive \
        --negatives-dir datasets/voice/wake_word/negative \
        --out models/versions/wake_word_xei_xata.onnx \
        --epochs 100

The user must record ~100 positive samples before running this script.
Negative samples are sourced from Speech Commands dataset (auto-downloaded).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from openwakeword.train import train_model  # type: ignore[import]

    OWW_TRAIN_AVAILABLE = True
except ImportError:
    OWW_TRAIN_AVAILABLE = False

try:
    import mlflow  # type: ignore[import]

    MLFLOW_AVAILABLE = True
except ImportError:
    MLFLOW_AVAILABLE = False


def train(
    samples_dir: Path,
    negatives_dir: Path | None,
    out_path: Path,
    epochs: int = 100,
    test_split: float = 0.2,
) -> dict[str, float]:
    """Run openWakeWord training. Returns metrics dict."""
    if not OWW_TRAIN_AVAILABLE:
        raise ImportError(
            "openwakeword training not installed. " "Run: pip install openwakeword[train]"
        )
    if not samples_dir.exists() or not any(samples_dir.glob("*.wav")):
        raise FileNotFoundError(
            f"No .wav samples found in {samples_dir}. " "Record ~100 samples of 'хей хата' first."
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # openWakeWord training API (as of v0.6+)
    metrics: dict[str, float] = train_model(
        positive_dir=str(samples_dir),
        negative_dir=str(negatives_dir) if negatives_dir else None,
        output_model_path=str(out_path),
        n_epochs=epochs,
        val_split=test_split,
    )
    return metrics


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Train 'хей хата' wake word model")
    parser.add_argument("--samples-dir", required=True, type=Path)
    parser.add_argument("--negatives-dir", default=None, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--test-split", type=float, default=0.2)
    parser.add_argument("--mlflow-uri", default=None)
    args = parser.parse_args(argv)

    if MLFLOW_AVAILABLE and args.mlflow_uri:
        mlflow.set_tracking_uri(args.mlflow_uri)
        with mlflow.start_run(run_name="wake_word_xei_xata"):
            mlflow.log_params({"epochs": args.epochs, "test_split": args.test_split})
            metrics = train(
                args.samples_dir, args.negatives_dir, args.out, args.epochs, args.test_split
            )
            mlflow.log_metrics(metrics)
            mlflow.log_artifact(str(args.out))
    else:
        metrics = train(
            args.samples_dir, args.negatives_dir, args.out, args.epochs, args.test_split
        )

    logger.info("Training complete. Metrics: %s", metrics)
    logger.info("Model saved to: %s", args.out)


if __name__ == "__main__":
    main(sys.argv[1:])
