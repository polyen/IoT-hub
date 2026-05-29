"""Unit tests for training/intent_classifier/prepare_dataset.py.

Mocks MASSIVE download so tests run offline (no `datasets` install required).
Verifies:
- INTENT_LABELS / MASSIVE_INTENT_MAP integrity (every mapped label is canonical)
- synthetic generator yields non-empty per-class examples
- stratified split keeps class distribution and forces manual examples into test
- final JSONL output has the expected shape
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from training.intent_classifier.intents import INTENT_LABELS, MASSIVE_INTENT_MAP
from training.intent_classifier.prepare_dataset import (
    generate_synthetic,
    load_manual,
    main,
    stratified_split,
    write_jsonl,
)
from training.intent_classifier.templates import GENERATORS

# ---------------------------------------------------------------------------
# Static invariants
# ---------------------------------------------------------------------------


def test_massive_map_targets_are_canonical() -> None:
    """Every MASSIVE_INTENT_MAP value must exist in INTENT_LABELS."""
    bad = [v for v in MASSIVE_INTENT_MAP.values() if v not in INTENT_LABELS]
    assert not bad, f"MASSIVE_INTENT_MAP maps to unknown labels: {bad}"


def test_every_canonical_intent_has_generator_or_is_runtime_only() -> None:
    """Every INTENT_LABELS entry needs synthetic examples, except ask_clarification
    which is a runtime-only fallback never seen in training data."""
    runtime_only = {"ask_clarification"}
    missing = [lbl for lbl in INTENT_LABELS if lbl not in GENERATORS and lbl not in runtime_only]
    assert not missing, f"Intents without a generator (model won't learn them): {missing}"


def test_generators_produce_non_empty_lists() -> None:
    """A regression guard against empty Cartesian products from missing slot lists."""
    for intent, gen in GENERATORS.items():
        examples = gen()
        assert len(examples) > 0, f"Generator {intent!r} produced 0 examples"
        # All examples must be lowercase strings (model expects normalised input)
        assert all(isinstance(x, str) and x == x.lower() for x in examples[:5])


# ---------------------------------------------------------------------------
# Synthetic generation
# ---------------------------------------------------------------------------


def test_generate_synthetic_caps_per_class() -> None:
    rows = generate_synthetic(per_class=3, seed=42)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["intent"]] = counts.get(r["intent"], 0) + 1
    for intent, n in counts.items():
        assert n <= 3, f"intent {intent!r} has {n} > 3 examples"


def test_generate_synthetic_is_deterministic() -> None:
    a = generate_synthetic(per_class=5, seed=42)
    b = generate_synthetic(per_class=5, seed=42)
    assert a == b


def test_generate_synthetic_marks_source() -> None:
    rows = generate_synthetic(per_class=2, seed=42)
    assert rows
    assert all(r["source"] == "synthetic" for r in rows)


# ---------------------------------------------------------------------------
# Manual examples loader
# ---------------------------------------------------------------------------


def test_load_manual_returns_empty_when_missing(tmp_path: Path) -> None:
    assert load_manual(tmp_path / "nope.jsonl") == []


def test_load_manual_parses_and_overrides_source(tmp_path: Path) -> None:
    path = tmp_path / "manual.jsonl"
    path.write_text(
        '{"text": "увімкни світло", "intent": "light_on"}\n'
        "# comment line\n"
        "\n"
        '{"text": "вимкни лампу", "intent": "light_off", "source": "user"}\n'
    )
    rows = load_manual(path)
    assert len(rows) == 2
    assert all(r["source"] == "manual" for r in rows), "Must overwrite source field"


# ---------------------------------------------------------------------------
# Stratified split
# ---------------------------------------------------------------------------


def test_stratified_split_keeps_each_class_in_train() -> None:
    rows = [
        {"text": f"text {i}", "intent": "light_on", "source": "synthetic"} for i in range(50)
    ] + [{"text": f"t {i}", "intent": "light_off", "source": "synthetic"} for i in range(50)]
    train, val, test = stratified_split(rows, val_size=0.1, test_size=0.1, seed=1)
    train_intents = {r["intent"] for r in train}
    assert train_intents == {"light_on", "light_off"}


def test_stratified_split_forces_manual_into_test() -> None:
    rows = [{"text": f"t{i}", "intent": "light_on", "source": "synthetic"} for i in range(20)] + [
        {"text": "manual prod example", "intent": "light_on", "source": "manual"}
    ]
    _, _, test = stratified_split(rows, val_size=0.1, test_size=0.1, seed=1)
    manual_in_test = [r for r in test if r["source"] == "manual"]
    assert len(manual_in_test) == 1


# ---------------------------------------------------------------------------
# JSONL output
# ---------------------------------------------------------------------------


def test_write_jsonl_roundtrip(tmp_path: Path) -> None:
    rows = [{"text": "тест", "intent": "light_on", "source": "synthetic"}]
    path = tmp_path / "out.jsonl"
    write_jsonl(rows, path)
    parsed = [json.loads(line) for line in path.read_text().splitlines()]
    assert parsed == rows


# ---------------------------------------------------------------------------
# CLI integration (offline)
# ---------------------------------------------------------------------------


def test_cli_runs_offline_and_writes_three_splits(tmp_path: Path) -> None:
    """--no-massive lets us exercise the full pipeline without HuggingFace access."""
    rc = main(
        [
            "--out-dir",
            str(tmp_path),
            "--synthetic-per-class",
            "5",
            "--no-massive",
            "--seed",
            "0",
        ]
    )
    assert rc == 0
    for split in ("train.jsonl", "val.jsonl", "test.jsonl", "stats.json"):
        assert (tmp_path / split).exists(), f"missing {split}"

    # stats.json is structurally valid
    stats = json.loads((tmp_path / "stats.json").read_text())
    assert "by_intent" in stats and "splits" in stats
    # Each generator-backed intent must have ≥1 row in the dataset.  We don't
    # check >= per-class here because some generators (summary_*, scene_*) have
    # very small template sets — the cap is an upper bound, not a floor.
    by_intent = stats["by_intent"]
    for intent in GENERATORS:
        assert by_intent.get(intent, 0) >= 1, f"intent {intent!r} has 0 examples"


@pytest.mark.parametrize(
    "intent_label",
    [
        "light_on",
        "light_off",
        "query_temperature",
        "scene_generic",
        "thermostat_set",
    ],
)
def test_each_critical_intent_has_at_least_one_example(intent_label: str, tmp_path: Path) -> None:
    """Sanity check that the pipeline produces ≥1 example for each headline intent."""
    main(["--out-dir", str(tmp_path), "--synthetic-per-class", "5", "--no-massive"])
    rows = [json.loads(line) for line in (tmp_path / "train.jsonl").read_text().splitlines()]
    rows.extend(json.loads(line) for line in (tmp_path / "test.jsonl").read_text().splitlines())
    rows.extend(json.loads(line) for line in (tmp_path / "val.jsonl").read_text().splitlines())
    assert any(r["intent"] == intent_label for r in rows)
