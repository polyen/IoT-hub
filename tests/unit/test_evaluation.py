"""Unit tests for T5.1 evaluation harness."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import yaml
from training.evaluation.cv_fire_smoke import EvalConfig, FireSmokeEvaluator, compute_iou
from training.evaluation.report import EvaluationReport

# ──────────────────────────────────────────────────────────────
# compute_iou tests
# ──────────────────────────────────────────────────────────────


def test_compute_iou_overlap() -> None:
    """Overlapping boxes should produce IoU > 0."""
    box1 = [0.0, 0.0, 100.0, 100.0]
    box2 = [50.0, 50.0, 150.0, 150.0]
    iou = compute_iou(box1, box2)
    assert iou > 0, f"Expected IoU > 0 for overlapping boxes, got {iou}"


def test_compute_iou_no_overlap() -> None:
    """Non-overlapping boxes should produce IoU == 0."""
    box1 = [0.0, 0.0, 10.0, 10.0]
    box2 = [20.0, 20.0, 30.0, 30.0]
    iou = compute_iou(box1, box2)
    assert iou == 0.0, f"Expected IoU == 0 for non-overlapping boxes, got {iou}"


def test_compute_iou_identical_boxes() -> None:
    """Identical boxes should produce IoU == 1.0."""
    box = [10.0, 10.0, 50.0, 50.0]
    iou = compute_iou(box, box)
    assert abs(iou - 1.0) < 1e-6, f"Expected IoU == 1.0 for identical boxes, got {iou}"


def test_compute_iou_partial_overlap() -> None:
    """Partially overlapping boxes should have 0 < IoU < 1."""
    box1 = [0.0, 0.0, 4.0, 4.0]
    box2 = [2.0, 0.0, 6.0, 4.0]
    iou = compute_iou(box1, box2)
    assert 0 < iou < 1, f"Expected 0 < IoU < 1 for partial overlap, got {iou}"


# ──────────────────────────────────────────────────────────────
# Query YAML balance test
# ──────────────────────────────────────────────────────────────


def test_balance_queries_by_category() -> None:
    """queries.yaml must contain exactly 100 queries with 25 per category."""
    yaml_path = Path(__file__).parent.parent.parent / "training" / "llm_eval" / "queries.yaml"
    assert yaml_path.exists(), f"queries.yaml not found at {yaml_path}"

    with open(yaml_path) as f:
        queries = yaml.safe_load(f)

    assert isinstance(queries, list), "queries.yaml must contain a list"
    assert len(queries) == 100, f"Expected 100 queries, got {len(queries)}"

    categories: dict[str, int] = {}
    for q in queries:
        cat = q.get("category", "unknown")
        categories[cat] = categories.get(cat, 0) + 1

    expected_cats = {"deterministic", "structured", "creative", "unknown"}
    assert (
        set(categories.keys()) == expected_cats
    ), f"Expected categories {expected_cats}, got {set(categories.keys())}"

    for cat, count in categories.items():
        assert count == 25, f"Category '{cat}' has {count} queries, expected 25"


# ──────────────────────────────────────────────────────────────
# EvaluationReport tests
# ──────────────────────────────────────────────────────────────


def test_report_markdown_contains_table() -> None:
    """EvaluationReport._to_markdown() must contain table markers."""
    report = EvaluationReport()
    report.add("cv_fire_smoke", {"mAP50": 0.82, "pass": True, "target": 0.78, "baseline": 0.32})
    report.add("cv_fall", {"F1": 0.85, "pass": True, "target": 0.80})

    md = report._to_markdown()
    assert "| " in md, "Markdown must contain table pipe characters"
    assert "|---" in md or "|-----" in md, "Markdown must contain table separator row"
    assert "mAP" in md or "Fire" in md, "Markdown must contain metric names"


def test_report_markdown_pass_fail_symbols() -> None:
    """Report markdown should include ✓ for pass and ✗ for fail."""
    report = EvaluationReport()
    report.add("cv_fire_smoke", {"mAP50": 0.82, "pass": True, "target": 0.78})
    report.add("cv_fall", {"F1": 0.70, "pass": False, "target": 0.80})

    md = report._to_markdown()
    assert "✓" in md, "Markdown should contain ✓ for passing metrics"
    assert "✗" in md, "Markdown should contain ✗ for failing metrics"


def test_find_next_version_empty() -> None:
    """Empty results directory → version 1."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ver = EvaluationReport.find_next_version(Path(tmpdir))
    assert ver == 1, f"Expected version 1 for empty dir, got {ver}"


def test_find_next_version_existing() -> None:
    """results_v3.json exists → next version is 4."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "results_v1.json").write_text("{}")
        (tmp / "results_v2.json").write_text("{}")
        (tmp / "results_v3.json").write_text("{}")
        ver = EvaluationReport.find_next_version(tmp)
    assert ver == 4, f"Expected version 4, got {ver}"


def test_find_next_version_non_sequential() -> None:
    """Non-sequential versions: v1, v5 → next is 6."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "results_v1.json").write_text("{}")
        (tmp / "results_v5.json").write_text("{}")
        ver = EvaluationReport.find_next_version(tmp)
    assert ver == 6, f"Expected version 6, got {ver}"


def test_report_save_creates_files() -> None:
    """save() should create both JSON and Markdown files."""
    report = EvaluationReport()
    report.add("cv_fire_smoke", {"mAP50": 0.80, "pass": True, "target": 0.78, "baseline": 0.32})

    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir)
        report.save(out)
        json_files = list(out.glob("results_v*.json"))
        md_files = list(out.glob("results_v*.md"))

    assert len(json_files) == 1, "Expected exactly one JSON result file"
    assert len(md_files) == 1, "Expected exactly one Markdown result file"


def test_report_json_content() -> None:
    """JSON output must be valid and contain sub-result keys."""
    report = EvaluationReport()
    report.add("cv_fire_smoke", {"mAP50": 0.82, "pass": True})
    report.add("stt_latency", {"pass": True, "speedup": 10.0})

    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir)
        report.save(out)
        json_file = list(out.glob("results_v*.json"))[0]
        data = json.loads(json_file.read_text())

    assert "cv_fire_smoke" in data
    assert "stt_latency" in data
    assert data["cv_fire_smoke"]["mAP50"] == 0.82


# ──────────────────────────────────────────────────────────────
# FireSmokeEvaluator compute_map tests
# ──────────────────────────────────────────────────────────────


def test_compute_map_perfect_predictions() -> None:
    """Perfect predictions (exact match) should yield mAP ≈ 1.0."""
    config = EvalConfig(model_path="", dataset_dir="")
    evaluator = FireSmokeEvaluator(config)

    gt = {"img1": [{"class_id": 0, "bbox": [10.0, 10.0, 50.0, 50.0]}]}
    pred = {"img1": [{"class_id": 0, "confidence": 0.95, "bbox": [10.0, 10.0, 50.0, 50.0]}]}
    map50 = evaluator.compute_map(pred, gt, iou_thresh=0.5)
    assert map50 > 0.9, f"Expected mAP ≈ 1.0 for perfect predictions, got {map50}"


def test_compute_map_no_predictions() -> None:
    """No predictions → mAP == 0 when ground truth exists."""
    config = EvalConfig(model_path="", dataset_dir="")
    evaluator = FireSmokeEvaluator(config)

    gt = {"img1": [{"class_id": 0, "bbox": [10.0, 10.0, 50.0, 50.0]}]}
    pred: dict = {}
    map50 = evaluator.compute_map(pred, gt, iou_thresh=0.5)
    assert map50 == 0.0, f"Expected mAP == 0 for no predictions, got {map50}"
