"""Prepare training data for the intent classifier.

Combines:
1. MASSIVE uk-UA subset (Amazon, arXiv 2204.08582) — real utterances with
   ground-truth intent labels.
2. Synthetic templates (templates.py) — Cartesian expansion to top up rare
   classes.
3. Optional manual examples from data/intent_classifier/manual.jsonl
   (real misclassifications harvested from prod telemetry).

Output: data/intent_classifier/{train,val,test}.jsonl
Each row: {"text": str, "intent": str, "source": "massive"|"synthetic"|"manual"}

Usage:
    uv run python -m training.intent_classifier.prepare_dataset \
        --out-dir data/intent_classifier \
        --massive-locale uk-UA \
        --synthetic-per-class 200
"""

from __future__ import annotations

import argparse
import json
import logging
import random
from collections import Counter
from pathlib import Path
from typing import Any

from training.intent_classifier.intents import INTENT_LABELS, MASSIVE_INTENT_MAP
from training.intent_classifier.templates import GENERATORS

logger = logging.getLogger(__name__)


def load_massive(locale: str = "uk-UA") -> list[dict[str, str]]:
    """Download MASSIVE locale subset and map intents to our canonical labels.

    Returns a list of dicts ``{text, intent, source="massive"}``.  Skips
    examples whose original intent is not in MASSIVE_INTENT_MAP.

    Requires ``datasets`` library (in pyproject training extra).
    """
    try:
        from datasets import load_dataset  # type: ignore[import]
    except ImportError as exc:
        raise ImportError("datasets library not installed. Run: uv sync --extra training") from exc

    logger.info("Loading MASSIVE locale %s from HuggingFace...", locale)
    ds = load_dataset("AmazonScience/massive", locale)

    out: list[dict[str, str]] = []
    skipped: Counter[str] = Counter()

    for split_name in ("train", "validation", "test"):
        if split_name not in ds:
            continue
        for ex in ds[split_name]:
            text = str(ex.get("utt", "")).strip().lower()
            original_intent = str(ex.get("intent", ""))
            mapped = MASSIVE_INTENT_MAP.get(original_intent)
            if mapped is None:
                skipped[original_intent] += 1
                continue
            out.append({"text": text, "intent": mapped, "source": "massive"})

    logger.info(
        "MASSIVE: kept %d, skipped %d (top-skipped: %s)",
        len(out),
        sum(skipped.values()),
        skipped.most_common(5),
    )
    return out


def generate_synthetic(per_class: int = 200, seed: int = 42) -> list[dict[str, str]]:
    """Cartesian expansion from templates.GENERATORS, sampled to ``per_class`` each.

    Many template combinations are millions of strings; we shuffle once and
    keep the first ``per_class`` to avoid skew toward the early enumeration.
    """
    rng = random.Random(seed)
    out: list[dict[str, str]] = []
    for intent, generator in GENERATORS.items():
        examples = generator()
        rng.shuffle(examples)
        examples = examples[:per_class]
        for text in examples:
            out.append({"text": text, "intent": intent, "source": "synthetic"})
    logger.info("Synthetic: generated %d examples (target %d/class)", len(out), per_class)
    return out


def load_manual(path: Path) -> list[dict[str, str]]:
    """Load manually-authored examples (one JSON per line) if file exists."""
    if not path.exists():
        return []
    out: list[dict[str, str]] = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            row = json.loads(line)
            row["source"] = "manual"
            out.append(row)
    logger.info("Manual: loaded %d examples from %s", len(out), path)
    return out


def stratified_split(
    rows: list[dict[str, str]],
    val_size: float = 0.1,
    test_size: float = 0.1,
    seed: int = 42,
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    """Split rows by intent to keep class distribution balanced across splits.

    Manual examples are FORCED into the test split — they exist specifically
    to evaluate against real prod misclassifications.
    """
    rng = random.Random(seed)
    by_intent: dict[str, list[dict[str, str]]] = {}
    test_forced: list[dict[str, str]] = []
    for row in rows:
        if row["source"] == "manual":
            test_forced.append(row)
            continue
        by_intent.setdefault(row["intent"], []).append(row)

    train: list[dict[str, str]] = []
    val: list[dict[str, str]] = []
    test: list[dict[str, str]] = []
    for _intent, intent_rows in by_intent.items():
        rng.shuffle(intent_rows)
        n = len(intent_rows)
        n_test = max(1, int(n * test_size))
        n_val = max(1, int(n * val_size))
        test.extend(intent_rows[:n_test])
        val.extend(intent_rows[n_test : n_test + n_val])
        train.extend(intent_rows[n_test + n_val :])
    test.extend(test_forced)
    rng.shuffle(train)
    rng.shuffle(val)
    rng.shuffle(test)
    return train, val, test


def write_jsonl(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=Path("data/intent_classifier"))
    parser.add_argument("--massive-locale", type=str, default="uk-UA")
    parser.add_argument("--synthetic-per-class", type=int, default=200)
    parser.add_argument("--val-size", type=float, default=0.1)
    parser.add_argument("--test-size", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--no-massive",
        action="store_true",
        help="Skip MASSIVE download (useful for offline tests).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    rows: list[dict[str, str]] = []
    if not args.no_massive:
        try:
            rows.extend(load_massive(args.massive_locale))
        except Exception as exc:  # noqa: BLE001
            logger.warning("MASSIVE download failed (%s) — continuing with synthetic only", exc)

    rows.extend(generate_synthetic(args.synthetic_per_class, seed=args.seed))
    rows.extend(load_manual(args.out_dir / "manual.jsonl"))

    # Sanity check: every canonical intent must have ≥1 row, else SetFit will choke
    counts = Counter(r["intent"] for r in rows)
    missing = [lbl for lbl in INTENT_LABELS if counts.get(lbl, 0) == 0]
    if missing:
        # ask_clarification is a runtime-only fallback, never seen in training data
        missing = [lbl for lbl in missing if lbl != "ask_clarification"]
        if missing:
            logger.warning(
                "Intents with no training data (model won't learn them): %s",
                missing,
            )

    train, val, test = stratified_split(rows, args.val_size, args.test_size, seed=args.seed)
    write_jsonl(train, args.out_dir / "train.jsonl")
    write_jsonl(val, args.out_dir / "val.jsonl")
    write_jsonl(test, args.out_dir / "test.jsonl")

    stats: dict[str, Any] = {
        "total": len(rows),
        "by_intent": dict(counts),
        "splits": {"train": len(train), "val": len(val), "test": len(test)},
    }
    (args.out_dir / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2))
    logger.info("Wrote splits to %s — %s", args.out_dir, stats["splits"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
