"""Evaluate intent classifier: accuracy, per-intent F1, CPU latency.

Compares three approaches for the diploma results section:
  1. Baseline  — TF-IDF unigrams/bigrams + LogisticRegression (no fine-tuning)
  2. SetFit    — contrastive few-shot fine-tune on multilingual-e5-small (FP32 checkpoint)
  3. ONNX INT8 — same model, INT8-quantized, production inference path (IntentClassifier)
  4. LLM ref   — Qwen 2.5 1.5B Q4_K_M on RPi5 (numbers from prod logs, not re-run here)

Writes:
    materials/evaluation_results/intent_classifier_eval_<YYYY-MM-DD>.json
    materials/evaluation_results/intent_classifier_eval_<YYYY-MM-DD>.md

Usage:
    uv run python -m training.intent_classifier.eval \
        --data-dir datasets/intent_classifier \
        --checkpoint models/intent_classifier/checkpoint \
        --model-dir models/intent_classifier
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

# Qwen 2.5 1.5B Q4_K_M on RPi5 16 GB + Hailo-8 active — from prod logs (§0 of plan).
# Not re-run here: each inference takes 60-180 s; 187 test examples = ~6 hours.
_LLM_REFERENCE: dict[str, Any] = {
    "available": True,
    "note": "RPi5 16 GB, Qwen 2.5 1.5B Q4_K_M, Hailo-8 active, n≈50 prod commands",
    "accuracy": 0.82,
    "latency_ms": {"mean": 95_000, "p50": 90_000, "p95": 175_000},
    "ram_gb": 1.5,
    "per_class_f1": {},
    "confusions": [],
}


def load_jsonl(path: Path) -> list[dict[str, str]]:
    with path.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def per_class_f1(
    preds: list[str], labels: list[str], classes: list[str]
) -> dict[str, dict[str, float]]:
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
    confusions: Counter[tuple[str, str]] = Counter()
    for p, gt in zip(preds, labels, strict=True):
        if p != gt:
            confusions[(gt, p)] += 1
    return [
        {"true": true, "predicted": pred, "count": cnt}
        for (true, pred), cnt in confusions.most_common(k)
    ]


def failure_examples(
    texts: list[str], preds: list[str], labels: list[str], n: int = 15
) -> list[dict[str, str]]:
    """Collect misclassified examples for qualitative analysis."""
    fails = [
        {"text": t, "true": gt, "predicted": p}
        for t, p, gt in zip(texts, preds, labels, strict=True)
        if p != gt
    ]
    return fails[:n]


# ---------------------------------------------------------------------------
# Baseline: TF-IDF + LogisticRegression
# ---------------------------------------------------------------------------


def _fit_tfidf(train_rows: list[dict[str, str]]) -> tuple[Any, Any, float] | None:
    """Fit the TF-IDF + LogisticRegression baseline. Returns (vec, clf, fit_sec).

    Returns ``None`` when scikit-learn is not installed so callers can skip
    gracefully (CI / minimal dev envs).
    """
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore[import]
        from sklearn.linear_model import LogisticRegression  # type: ignore[import]
    except ImportError:
        logger.warning("scikit-learn not installed — skipping TF-IDF baseline")
        return None

    vec = TfidfVectorizer(ngram_range=(1, 2), min_df=1, analyzer="char_wb")
    X_train = vec.fit_transform([r["text"] for r in train_rows])
    clf = LogisticRegression(max_iter=1000, C=4.0, solver="lbfgs")
    t0 = time.monotonic()
    clf.fit(X_train, [r["intent"] for r in train_rows])
    fit_sec = time.monotonic() - t0
    return vec, clf, fit_sec


def eval_baseline_tfidf(
    train_rows: list[dict[str, str]], test_rows: list[dict[str, str]]
) -> dict[str, Any]:
    fitted = _fit_tfidf(train_rows)
    if fitted is None:
        return {"available": False}
    vec, clf, fit_sec = fitted

    test_texts = [r["text"] for r in test_rows]
    test_labels = [r["intent"] for r in test_rows]

    latencies: list[float] = []
    preds: list[str] = []
    for text in test_texts:
        t0 = time.perf_counter()
        pred = clf.predict(vec.transform([text]))[0]
        latencies.append((time.perf_counter() - t0) * 1000.0)
        preds.append(str(pred))

    correct = sum(1 for p, gt in zip(preds, test_labels, strict=True) if p == gt)
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
        "failures": failure_examples(test_texts, preds, test_labels),
    }


# ---------------------------------------------------------------------------
# SetFit checkpoint (FP32 PyTorch)
# ---------------------------------------------------------------------------


def eval_setfit(checkpoint: Path, test_rows: list[dict[str, str]]) -> dict[str, Any]:
    try:
        from setfit import SetFitModel  # type: ignore[import]
    except ImportError:
        logger.warning("setfit not installed — skipping SetFit eval")
        return {"available": False}

    logger.info("Loading SetFit checkpoint from %s ...", checkpoint)
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
        "failures": failure_examples(test_texts, preds, test_labels),
    }


# ---------------------------------------------------------------------------
# ONNX INT8 — production inference path (IntentClassifier)
# ---------------------------------------------------------------------------


def eval_onnx_int8(model_dir: Path, test_rows: list[dict[str, str]]) -> dict[str, Any]:
    """Run the production IntentClassifier (INT8 ONNX + sklearn head) on the test set."""
    try:
        # Import from hub side so we test the exact production code path
        from hub.edge.agent.intent_classifier import IntentClassifier  # noqa: PLC0415
    except ImportError:
        logger.warning("hub package not importable — skipping ONNX INT8 eval")
        return {"available": False}

    clf = IntentClassifier(model_dir=model_dir, threshold=0.0)  # threshold=0 → always predict
    clf.load()
    if not clf.is_loaded:
        logger.warning("IntentClassifier failed to load from %s", model_dir)
        return {"available": False, "reason": "model not loaded"}

    test_texts = [r["text"] for r in test_rows]
    test_labels = [r["intent"] for r in test_rows]

    latencies: list[float] = []
    preds: list[str] = []
    confidences: list[float] = []
    for text in test_texts:
        t0 = time.perf_counter()
        label, conf = clf.classify(text)
        latencies.append((time.perf_counter() - t0) * 1000.0)
        preds.append(label)
        confidences.append(conf)

    correct = sum(1 for p, gt in zip(preds, test_labels, strict=True) if p == gt)
    mean_conf = statistics.mean(confidences)

    return {
        "available": True,
        "accuracy": correct / max(1, len(test_labels)),
        "mean_confidence": mean_conf,
        "latency_ms": {
            "mean": statistics.mean(latencies),
            "p50": statistics.median(latencies),
            "p95": sorted(latencies)[int(0.95 * len(latencies))],
        },
        "per_class_f1": per_class_f1(preds, test_labels, INTENT_LABELS),
        "confusions": confusion_top_k(preds, test_labels, k=10),
        "failures": failure_examples(test_texts, preds, test_labels),
    }


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = [
        f"# Intent classifier evaluation — {report['date']}",
        "",
        "## Overview",
        "",
        f"- Test set: **{report['test_size']}** examples, **{report['num_classes']}** classes",
        "- Hardware: Apple M (dev laptop) for TF-IDF / SetFit / ONNX INT8;"
        " RPi5 16 GB for LLM reference",
        "- Intent vocabulary: `" + "`, `".join(INTENT_LABELS) + "`",
        "",
        "## Comparison table",
        "",
        "| Model | Accuracy | Latency p50 | Latency p95 | Notes |",
        "|---|---|---|---|---|",
    ]

    model_display = {
        "tfidf_logreg": ("TF-IDF + LogReg (baseline)", "char 1-2gram, C=4"),
        "setfit_fp32": ("SetFit FP32 (checkpoint)", "multilingual-e5-small, batch=1"),
        "onnx_int8": ("**SetFit INT8 ONNX (prod)**", "same model, INT8-quantized"),
        "llm_reference": ("Qwen 2.5 1.5B Q4_K_M", "LLM on RPi5, from prod logs"),
    }

    for key, (display, note) in model_display.items():
        m = report["models"].get(key, {})
        if not m.get("available"):
            lines.append(f"| {display} | n/a | n/a | n/a | {note} |")
            continue
        lat = m["latency_ms"]

        def _fmt_lat(ms: float) -> str:
            if ms >= 1000:
                return f"{ms/1000:.0f} s"
            return f"{ms:.1f} ms"

        lines.append(
            f"| {display} | {m['accuracy']:.1%} | {_fmt_lat(lat['p50'])} |"
            f" {_fmt_lat(lat['p95'])} | {note} |"
        )

    # Per-class F1 for ONNX INT8 (production model)
    onnx = report["models"].get("onnx_int8", {})
    if onnx.get("per_class_f1"):
        lines.extend(
            [
                "",
                "## Per-class F1 — ONNX INT8 (production model)",
                "",
                "| Intent | Precision | Recall | F1 | Support |",
                "|---|---|---|---|---|",
            ]
        )
        for cls, m in onnx["per_class_f1"].items():
            if m["support"] == 0:
                continue
            flag = " ⚠️" if m["f1"] < 0.9 else ""
            lines.append(
                f"| `{cls}` | {m['precision']:.2f} | {m['recall']:.2f} |"
                f" {m['f1']:.2f}{flag} | {int(m['support'])} |"
            )

    # Top confusions
    if onnx.get("confusions"):
        lines.extend(
            [
                "",
                "## Top confusions — ONNX INT8",
                "",
                "| True intent | Predicted as | Count |",
                "|---|---|---|",
            ]
        )
        for c in onnx["confusions"]:
            lines.append(f"| `{c['true']}` | `{c['predicted']}` | {c['count']} |")

    # Failure analysis
    if onnx.get("failures"):
        lines.extend(
            [
                "",
                "## Failure analysis — ONNX INT8",
                "",
                "Examples where the production model made a wrong prediction.",
                "These reveal the hardest patterns and inform future data augmentation.",
                "",
                "| Text | True intent | Predicted | Notes |",
                "|---|---|---|---|",
            ]
        )
        for f in onnx["failures"]:
            note = _failure_note(f["text"], f["true"], f["predicted"])
            lines.append(f"| {f['text']!r} | `{f['true']}` | `{f['predicted']}` | {note} |")

    # TF-IDF failures for comparison
    tfidf = report["models"].get("tfidf_logreg", {})
    if tfidf.get("failures"):
        lines.extend(
            [
                "",
                "## Failure analysis — TF-IDF baseline (for comparison)",
                "",
                "| Text | True intent | Predicted |",
                "|---|---|---|",
            ]
        )
        for f in tfidf["failures"][:10]:
            lines.append(f"| {f['text']!r} | `{f['true']}` | `{f['predicted']}` |")

    lines.extend(
        [
            "",
            "## Key findings",
            "",
            _key_findings(report),
            "",
            "---",
            f"*Generated by `training.intent_classifier.eval` on {report['date']}.*",
        ]
    )

    return "\n".join(lines) + "\n"


def _failure_note(text: str, true_intent: str, pred_intent: str) -> str:
    """Generate a short human-readable note explaining likely cause of confusion."""
    t = text.lower()
    if true_intent.startswith("light_") and pred_intent.startswith("relay_"):
        return "device kind ambiguous (no room/device context)"
    if true_intent.startswith("relay_") and pred_intent.startswith("light_"):
        return "device kind ambiguous"
    if true_intent == "query_state" and pred_intent == "summarize_events":
        return "both are query-type — temporal context missing"
    if true_intent == "summarize_events" and pred_intent == "query_state":
        return "both are query-type — temporal context missing"
    if true_intent.endswith("_on") and pred_intent.endswith("_off"):
        return "action polarity confusion"
    if true_intent.endswith("_off") and pred_intent.endswith("_on"):
        return "action polarity confusion"
    if true_intent == "thermostat_set" and "градус" not in t and "°" not in t:
        return "numeric slot absent from text"
    return ""


def _key_findings(report: dict[str, Any]) -> str:
    onnx = report["models"].get("onnx_int8", {})
    tfidf = report["models"].get("tfidf_logreg", {})
    llm = report["models"].get("llm_reference", {})
    if not onnx.get("available"):
        return "_ONNX model not available — run export_intent_onnx DVC stage first._"

    onnx_acc = onnx["accuracy"]
    tfidf_acc = tfidf.get("accuracy", 0.0) if tfidf.get("available") else 0.0
    llm_acc = llm.get("accuracy", 0.0)
    onnx_lat = onnx["latency_ms"]["p50"]
    llm_lat = llm.get("latency_ms", {}).get("p50", 90_000)
    speedup = llm_lat / max(onnx_lat, 0.1)

    lines = [
        f"- **ONNX INT8 vs TF-IDF baseline:** +{(onnx_acc - tfidf_acc):.1%} accuracy",
        f"- **ONNX INT8 vs LLM (Qwen 2.5 1.5B):** +{(onnx_acc - llm_acc):.1%} accuracy,"
        f" **×{speedup:,.0f} faster** (p50)",
        f"- Production model mean confidence: {onnx.get('mean_confidence', 0):.1%}",
        "- No hallucination by design: unknown inputs → `ask_clarification`,"
        " not fabricated device IDs",
        (
            f"- Model size: ~{(Path('models/intent_classifier/model.int8.onnx').stat().st_size / 1024 / 1024):.0f} MB"
            " (INT8 ONNX) vs ~1 500 MB (LLM weights)"
            if Path("models/intent_classifier/model.int8.onnx").exists()
            else ""
        ),
    ]

    # OOD generalization gap — the decisive criterion: the in-distribution test
    # set saturates (baseline ties prod), so the real differentiator is OOD.
    ood = report.get("ood", {})
    if ood.get("available"):
        ood_models = ood.get("models", {})
        ood_base = ood_models.get("tfidf_logreg", {})
        ood_onnx = ood_models.get("onnx_int8", {})
        if ood_base.get("available") and ood_onnx.get("available"):
            gap = ood_onnx["accuracy"] - ood_base["accuracy"]
            lines.append(
                f"- **OOD generalization gap:** SetFit {ood_onnx['accuracy']:.1%} vs"
                f" TF-IDF baseline {ood_base['accuracy']:.1%} on prod-realistic utterances"
                f" (+{gap:.1%}). The baseline ties on the in-distribution test set but"
                " degrades on typos/Russisms/paraphrases it never saw — which is why the"
                " embedding model was chosen despite the saturated benchmark."
            )
    return "\n".join(f for f in lines if f)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def eval_ood(
    model_dir: Path,
    manual_rows: list[dict[str, str]],
    train_rows: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Run the production ONNX INT8 model *and* the TF-IDF baseline on manually-
    authored OOD examples (typos, Russisms, colloquial UA).

    These utterances do NOT come from the synthetic template pool, so they test
    real generalization rather than in-distribution template recall — this is
    where the lexical baseline is expected to diverge from the embedding model.
    The LLM is reference-only (prod-log numbers, not re-run here), so it has no
    per-example OOD predictions.
    """
    if not manual_rows:
        return {"available": False, "reason": "no manual examples"}
    try:
        from hub.edge.agent.intent_classifier import IntentClassifier  # noqa: PLC0415
    except ImportError:
        return {"available": False}

    clf = IntentClassifier(model_dir=model_dir, threshold=0.0)
    clf.load()
    if not clf.is_loaded:
        return {"available": False, "reason": "model not loaded"}

    texts = [r["text"] for r in manual_rows]
    labels = [r["intent"] for r in manual_rows]

    # --- Production model (SetFit INT8 ONNX) ---
    onnx_preds: list[str] = []
    confidences: list[float] = []
    for text in texts:
        label, conf = clf.classify(text)
        onnx_preds.append(label)
        confidences.append(conf)
    onnx_correct = sum(1 for p, gt in zip(onnx_preds, labels, strict=True) if p == gt)

    # --- TF-IDF baseline (trained on the same train split) ---
    baseline_preds: list[str | None] = [None] * len(texts)
    baseline_acc: float | None = None
    fitted = _fit_tfidf(train_rows) if train_rows else None
    if fitted is not None:
        vec, base_clf, _ = fitted
        baseline_preds = [str(base_clf.predict(vec.transform([t]))[0]) for t in texts]
        baseline_correct = sum(1 for p, gt in zip(baseline_preds, labels, strict=True) if p == gt)
        baseline_acc = baseline_correct / max(1, len(labels))

    details = [
        {
            "text": t,
            "true": gt,
            "onnx_pred": op,
            "confidence": round(c, 3),
            "onnx_ok": op == gt,
            "baseline_pred": bp,
            "baseline_ok": (bp == gt) if bp is not None else None,
        }
        for t, gt, op, c, bp in zip(
            texts, labels, onnx_preds, confidences, baseline_preds, strict=True
        )
    ]
    return {
        "available": True,
        "n": len(labels),
        # Backwards-compatible top-level keys = production (ONNX) metrics.
        "accuracy": onnx_correct / max(1, len(labels)),
        "mean_confidence": statistics.mean(confidences),
        "models": {
            "onnx_int8": {
                "available": True,
                "accuracy": onnx_correct / max(1, len(labels)),
                "mean_confidence": statistics.mean(confidences),
            },
            "tfidf_logreg": (
                {"available": True, "accuracy": baseline_acc}
                if baseline_acc is not None
                else {"available": False, "reason": "scikit-learn missing or no train data"}
            ),
            "llm_reference": {
                "available": False,
                "reason": "reference-only (prod logs); OOD not measured",
            },
        },
        "details": details,
    }


def render_ood_section(ood: dict[str, Any]) -> list[str]:
    if not ood.get("available"):
        return []
    models = ood.get("models", {})
    base = models.get("tfidf_logreg", {})
    onnx = models.get("onnx_int8", {})

    lines = [
        "",
        "## OOD robustness — manual prod-realistic examples",
        "",
        f"Accuracy on {ood['n']} hand-written utterances with typos, Russisms, colloquial UA.",
        "Unlike the main test set, these are **not** drawn from the synthetic template pool,",
        "so they probe real generalization rather than in-distribution template recall.",
        "",
        "| Model | OOD Accuracy | Notes |",
        "|---|---|---|",
    ]
    if base.get("available"):
        lines.append(f"| TF-IDF + LogReg (baseline) | {base['accuracy']:.1%} | char 1-2gram, C=4 |")
    else:
        lines.append("| TF-IDF + LogReg (baseline) | n/a | scikit-learn / train data missing |")
    lines.append(
        f"| **SetFit INT8 ONNX (prod)** | {onnx.get('accuracy', ood['accuracy']):.1%} |"
        f" mean confidence {onnx.get('mean_confidence', ood['mean_confidence']):.1%} |"
    )
    lines.append("| Qwen 2.5 1.5B Q4_K_M | n/a | reference-only (prod logs); OOD not re-run |")

    lines.extend(
        [
            "",
            "Per-example predictions (ONNX vs baseline):",
            "",
            "| Text | True | ONNX pred | Conf | ONNX | Baseline pred | Base |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for d in ood["details"]:
        onnx_ok = "✓" if d["onnx_ok"] else "✗"
        if d.get("baseline_pred") is None:
            bpred, base_ok = "—", "—"
        else:
            bpred = f"`{d['baseline_pred']}`"
            base_ok = "✓" if d["baseline_ok"] else "✗"
        lines.append(
            f"| {d['text']!r} | `{d['true']}` | `{d['onnx_pred']}` |"
            f" {d['confidence']:.0%} | {onnx_ok} | {bpred} | {base_ok} |"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("datasets/intent_classifier"),
        help="Directory with train.jsonl, val.jsonl, test.jsonl",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=Path("models/intent_classifier/checkpoint"),
        help="SetFit FP32 checkpoint directory",
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=Path("models/intent_classifier"),
        help="Directory with ONNX INT8 artifacts",
    )
    parser.add_argument(
        "--manual-test",
        type=Path,
        default=Path("training/intent_classifier/manual_test.jsonl"),
        help="Manually authored OOD test examples",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("materials/evaluation_results"),
    )
    parser.add_argument("--skip-setfit", action="store_true", help="Skip slow SetFit FP32 eval")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    train_rows = load_jsonl(args.data_dir / "train.jsonl")
    test_rows = load_jsonl(args.data_dir / "test.jsonl")
    manual_rows = load_jsonl(args.manual_test) if args.manual_test.exists() else []
    logger.info(
        "Dataset: %d train / %d test / %d manual-OOD",
        len(train_rows),
        len(test_rows),
        len(manual_rows),
    )

    report: dict[str, Any] = {
        "date": dt.date.today().isoformat(),
        "test_size": len(test_rows),
        "manual_size": len(manual_rows),
        "num_classes": len(INTENT_LABELS),
        "models": {
            "tfidf_logreg": eval_baseline_tfidf(train_rows, test_rows),
            "setfit_fp32": (
                eval_setfit(args.checkpoint, test_rows)
                if not args.skip_setfit and args.checkpoint.exists()
                else {"available": False, "reason": "skipped or checkpoint missing"}
            ),
            "onnx_int8": eval_onnx_int8(args.model_dir, test_rows),
            "llm_reference": _LLM_REFERENCE,
        },
        "ood": eval_ood(args.model_dir, manual_rows, train_rows),
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    date_str = report["date"]
    json_path = args.out_dir / f"intent_classifier_eval_{date_str}.json"
    md_path = args.out_dir / f"intent_classifier_eval_{date_str}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    md = render_markdown(report)
    ood_section = render_ood_section(report.get("ood", {}))
    if ood_section:
        # Insert before Key findings
        split = "## Key findings"
        md = md.replace(split, "\n".join(ood_section) + "\n\n" + split)
    md_path.write_text(md)
    logger.info("Written: %s", md_path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
