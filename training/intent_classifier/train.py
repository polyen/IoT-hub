"""Train intent classifier via SetFit on multilingual-e5-small.

SetFit is a few-shot framework that contrastively fine-tunes a sentence
transformer, then trains a sklearn LogisticRegression head on top of the
embeddings.  Cheap, fast, and gives 95-99% accuracy on small intent sets.

Outputs:
    models/intent_classifier/checkpoint/    — SetFit checkpoint
    models/intent_classifier/metadata.json  — labels + training stats
    reports/intent_classifier_metrics.json  — DVC metrics

MLflow tracking (params + metrics) — http://localhost:5001 by default.

Usage:
    uv run python -m training.intent_classifier.train \
        --data-dir data/intent_classifier \
        --out-dir models/intent_classifier
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from collections import Counter
from pathlib import Path
from typing import Any

from training.intent_classifier.intents import INTENT_LABELS

logger = logging.getLogger(__name__)


def load_jsonl(path: Path) -> list[dict[str, str]]:
    with path.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def build_label_index(rows: list[dict[str, str]]) -> tuple[list[int], list[str]]:
    """Map each row's intent to an int index using INTENT_LABELS order."""
    text_list: list[str] = []
    label_list: list[int] = []
    for r in rows:
        intent = r["intent"]
        if intent not in INTENT_LABELS:
            logger.warning("Skipping row with unknown intent %r", intent)
            continue
        text_list.append(r["text"])
        label_list.append(INTENT_LABELS.index(intent))
    return label_list, text_list


def train(
    data_dir: Path,
    out_dir: Path,
    base_model: str = "intfloat/multilingual-e5-small",
    num_iterations: int = 20,
    num_epochs: int = 1,
    batch_size: int = 16,
    learning_rate: float = 2e-5,
    mlflow_uri: str | None = None,
) -> dict[str, Any]:
    """Fine-tune SetFit and write checkpoint + metadata."""
    try:
        from setfit import SetFitModel, Trainer, TrainingArguments  # type: ignore[import]

        from datasets import Dataset  # type: ignore[import]
    except ImportError as exc:
        raise ImportError("setfit + datasets not installed. Run: uv sync --extra training") from exc

    train_rows = load_jsonl(data_dir / "train.jsonl")
    val_rows = load_jsonl(data_dir / "val.jsonl")
    test_rows = load_jsonl(data_dir / "test.jsonl")

    train_labels, train_texts = build_label_index(train_rows)
    val_labels, val_texts = build_label_index(val_rows)
    test_labels, test_texts = build_label_index(test_rows)

    logger.info(
        "Loaded %d train / %d val / %d test rows; class distribution: %s",
        len(train_texts),
        len(val_texts),
        len(test_texts),
        Counter(r["intent"] for r in train_rows).most_common(),
    )

    train_ds = Dataset.from_dict({"text": train_texts, "label": train_labels})
    val_ds = Dataset.from_dict({"text": val_texts, "label": val_labels})

    model = SetFitModel.from_pretrained(base_model, labels=INTENT_LABELS)

    args = TrainingArguments(
        batch_size=batch_size,
        num_iterations=num_iterations,
        num_epochs=num_epochs,
        body_learning_rate=learning_rate,
    )
    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        metric="accuracy",
    )

    # MLflow tracking (optional — non-fatal if server down)
    try:
        import mlflow  # type: ignore[import]

        if mlflow_uri:
            mlflow.set_tracking_uri(mlflow_uri)
        mlflow.set_experiment("intent_classifier")
        mlflow_run = mlflow.start_run()
        mlflow.log_params(
            {
                "base_model": base_model,
                "num_iterations": num_iterations,
                "num_epochs": num_epochs,
                "batch_size": batch_size,
                "learning_rate": learning_rate,
                "train_size": len(train_texts),
                "num_classes": len(INTENT_LABELS),
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("MLflow unavailable (%s) — continuing without tracking", exc)
        mlflow = None  # type: ignore[assignment]
        mlflow_run = None

    t0 = time.monotonic()
    trainer.train()
    train_duration_sec = time.monotonic() - t0

    val_metrics = trainer.evaluate()
    test_pred = model.predict(test_texts).tolist()
    # SetFit returns label-string predictions; convert back to indices
    test_pred_idx = [INTENT_LABELS.index(p) if isinstance(p, str) else p for p in test_pred]
    test_acc = sum(int(p == g) for p, g in zip(test_pred_idx, test_labels, strict=True)) / max(
        1, len(test_labels)
    )

    metrics = {
        "val_accuracy": float(val_metrics.get("accuracy", 0.0)),
        "test_accuracy": test_acc,
        "train_duration_sec": train_duration_sec,
        "num_train_examples": len(train_texts),
        "num_classes": len(INTENT_LABELS),
    }

    if mlflow is not None and mlflow_run is not None:
        try:
            mlflow.log_metrics(metrics)
            mlflow.end_run()
        except Exception:  # noqa: BLE001
            pass

    # Persist checkpoint + metadata
    ckpt_dir = out_dir / "checkpoint"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(ckpt_dir))

    metadata = {
        "labels": INTENT_LABELS,
        "base_model": base_model,
        "metrics": metrics,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (out_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))

    # DVC-tracked metrics file
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    (reports_dir / "intent_classifier_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2)
    )

    logger.info("Training done in %.1fs: %s", train_duration_sec, metrics)
    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/intent_classifier"))
    parser.add_argument("--out-dir", type=Path, default=Path("models/intent_classifier"))
    parser.add_argument("--base-model", type=str, default="intfloat/multilingual-e5-small")
    parser.add_argument("--num-iterations", type=int, default=20)
    parser.add_argument("--num-epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--mlflow-uri", type=str, default="http://localhost:5001")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    train(
        data_dir=args.data_dir,
        out_dir=args.out_dir,
        base_model=args.base_model,
        num_iterations=args.num_iterations,
        num_epochs=args.num_epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        mlflow_uri=args.mlflow_uri,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
