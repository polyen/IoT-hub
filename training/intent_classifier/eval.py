"""Evaluate intent classifier: accuracy, per-intent F1, CPU latency.

Compares:
- baseline: TF-IDF + scikit-learn LogisticRegression (cheap, for diploma table)
- candidate: trained SetFit checkpoint (or ONNX INT8 if available)

Writes:
    materials/evaluation_results/intent_classifier_eval_<YYYY-MM-DD>.json
    materials/evaluation_results/intent_classifier_eval_<YYYY-MM-DD>.md (table)

Usage:
    uv run python -m training.intent_classifier.eval \
        --data-dir data/intent_classifier \
        --checkpoint models/intent_classifier/checkpoint
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

from training.intent_classifier.intents import INTENT_LABELS

logger = logging.getLogger(__name__)


def load_jsonl(path: Path) -> list[dict[str, str]]:
    with path.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def per_class_f1(
    preds: list[str], labels: list[str], classes: list[str]
) -> dict[str, dict[str, float]]:
    """Compute precision/recall/F1 per class without sklearn."""
    out: dict[str, dict[str, float]] = {}
    for cls in classes:
        tp = sum(1 for p, gt in zip(preds, labels, strict=True) if p == cls and gt == cls)
        fp = sum(1 for p, gt in zip(preds, labels, strict=True) if p == cls and gt != cls)
        fn = sum(1 for p, gt in zip(preds, labels, strict=True) if p != cls and gt == cls)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        out[cls] = {"precision": precision, "recall": recall, "f1": f1, "support": tp + fn}
    return out


def confusion_top_k(preds: list[str], labels: list[str], k: int = 10) -> list[dict[str, Any]]:
    """Return the k most common (true_label, pred_label) confusion pairs."""
    confusions: Counter[tuple[str, str]] = Counter()
    for p, gt in zip(preds, labels, strict=True):
        if p != gt:
            confusions[(gt, p)] += 1
    return [
        {"true": true, "predicted": pred, "count": cnt}
        for (true, pred), cnt in confusions.most_common(k)
    ]


def eval_baseline_tfidf(
    train_rows: list[dict[str, str]], test_rows: list[dict[str, str]]
) -> dict[str, Any]:
    """TF-IDF + LogisticRegression — baseline for the diploma table."""
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore[import]
        from sklearn.linear_model import LogisticRegression  # type: ignore[import]
    except ImportError:
        logger.warning("scikit-learn not installed — skipping TF-IDF baseline")
        return {"available": False}

    train_texts = [r["text"] for r in train_rows]
    train_labels = [r["intent"] for r in train_rows]
    test_texts = [r["text"] for r in test_rows]
    test_labels = [r["intent"] for r in test_rows]

    vec = TfidfVectorizer(ngram_range=(1, 2), min_df=1)
    X_train = vec.fit_transform(train_texts)
    # Note: we re-vectorize per-test-example inside the latency loop below,
    # so no batch X_test here.

    clf = LogisticRegression(max_iter=500, C=4.0)
    t0 = time.monotonic()
    clf.fit(X_train, train_labels)
    fit_sec = time.monotonic() - t0

    # Latency on individual examples (single-row inference)
    latencies: list[float] = []
    correct = 0
    preds: list[str] = []
    for text, true_label in zip(test_texts, test_labels, strict=True):
        t0 = time.perf_counter()
        pred = clf.predict(vec.transform([text]))[0]
        latencies.append((time.perf_counter() - t0) * 1000.0)
        preds.append(pred)
        if pred == true_label:
            correct += 1

    return {
        "available": True,
        "accuracy": correct / max(1, len(test_labels)),
        "latency_ms": {
            "mean": statistics.mean(latencies),
            "p50": statistics.median(latencies),
            "p95": sorted(latencies)[int(0.95 * len(latencies))],
        },
        "fit_duration_sec": fit_sec,
        "per_class_f1": per_class_f1(preds, test_labels, INTENT_LABELS),
        "confusions": confusion_top_k(preds, test_labels, k=10),
    }


def eval_setfit(checkpoint: Path, test_rows: list[dict[str, str]]) -> dict[str, Any]:
    """Run SetFit checkpoint on test set with per-utterance latency."""
    try:
        from setfit import SetFitModel  # type: ignore[import]
    except ImportError:
        logger.warning("setfit not installed — skipping SetFit eval")
        return {"available": False}

    model = SetFitModel.from_pretrained(str(checkpoint))
    test_texts = [r["text"] for r in test_rows]
    test_labels = [r["intent"] for r in test_rows]

    latencies: list[float] = []
    preds: list[str] = []
    for text in test_texts:
        t0 = time.perf_counter()
        pred = model.predict([text])
        latencies.append((time.perf_counter() - t0) * 1000.0)
        p = pred[0] if hasattr(pred, "__getitem__") else pred
        preds.append(str(p))

    correct = sum(1 for p, gt in zip(preds, test_labels, strict=True) if p == gt)

    return {
        "available": True,
        "accuracy": correct / max(1, len(test_labels)),
        "latency_ms": {
            "mean": statistics.mean(latencies),
            "p50": statistics.median(latencies),
            "p95": sorted(latencies)[int(0.95 * len(latencies))],
        },
        "per_class_f1": per_class_f1(preds, test_labels, INTENT_LABELS),
        "confusions": confusion_top_k(preds, test_labels, k=10),
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Format eval report as a markdown table for diploma's results section."""
    lines: list[str] = [
        f"# Intent classifier eval — {report['date']}",
        "",
        "## Test set",
        f"- Size: {report['test_size']} examples",
        f"- Classes: {report['num_classes']}",
        "",
        "## Models compared",
        "",
        "| Model | Accuracy | Latency p50 (ms) | Latency p95 (ms) |",
        "|---|---|---|---|",
    ]
    for name, m in report["models"].items():
        if not m.get("available"):
            lines.append(f"| {name} | n/a | n/a | n/a |")
            continue
        lat = m["latency_ms"]
        lines.append(f"| {name} | {m['accuracy']:.3f} | {lat['p50']:.1f} | {lat['p95']:.1f} |")
    lines.extend(["", "## Per-class F1 (SetFit)", ""])
    setfit = report["models"].get("setfit", {})
    if setfit.get("per_class_f1"):
        lines.extend(["| Class | Precision | Recall | F1 | Support |", "|---|---|---|---|---|"])
        for cls, m in setfit["per_class_f1"].items():
            if m["support"] == 0:
                continue
            lines.append(
                f"| {cls} | {m['precision']:.2f} | {m['recall']:.2f} | "
                f"{m['f1']:.2f} | {int(m['support'])} |"
            )
    if setfit.get("confusions"):
        lines.extend(["", "## Top confusions (SetFit)", ""])
        lines.extend(["| True intent | Predicted | Count |", "|---|---|---|"])
        for c in setfit["confusions"]:
            lines.append(f"| {c['true']} | {c['predicted']} | {c['count']} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/intent_classifier"))
    parser.add_argument(
        "--checkpoint", type=Path, default=Path("models/intent_classifier/checkpoint")
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("materials/evaluation_results"),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    train_rows = load_jsonl(args.data_dir / "train.jsonl")
    test_rows = load_jsonl(args.data_dir / "test.jsonl")

    report: dict[str, Any] = {
        "date": dt.date.today().isoformat(),
        "test_size": len(test_rows),
        "num_classes": len(INTENT_LABELS),
        "models": {
            "tfidf_logreg": eval_baseline_tfidf(train_rows, test_rows),
            "setfit": (
                eval_setfit(args.checkpoint, test_rows)
                if args.checkpoint.exists()
                else {"available": False, "reason": "checkpoint missing"}
            ),
        },
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / f"intent_classifier_eval_{report['date']}.json"
    md_path = args.out_dir / f"intent_classifier_eval_{report['date']}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    md_path.write_text(render_markdown(report))
    logger.info("Eval written to %s and %s", json_path, md_path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
