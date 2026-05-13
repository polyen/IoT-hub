#!/usr/bin/env python3
"""Generate LLM benchmark comparison markdown from two Phase B JSON result files.

Usage:
    python scripts/bench_compare.py \\
        --baseline materials/evaluation_results/baseline/llm_bench_qwen2.5-3b-instruct.json \\
        --candidate materials/evaluation_results/qwen3.5/llm_bench_Qwen3.5-4B-Q4_K_M.json \\
        --out materials/evaluation_results/llm_bench_comparison_2026_05.md

Go/no-go criteria (calibrated from real RPi 5 16 GB run 2026-05-13):
  GO  = candidate accuracy >= baseline AND tok/s >= 2.5 AND latency_p95 <= 15 s
  SKIP = otherwise — document reason, try Phi-4 mini or Gemma 3 4B as alternatives

Latency threshold rationale: at 3.89 tok/s with max_tokens=256, a full completion
takes up to 66 s; typical tool-call responses (30-80 tokens) take 8-20 s.  The
original 8 s threshold was pre-hardware; 15 s p95 is the empirically validated
target for RPi 5 (baseline p95 = 11.94 s observed 2026-05-13).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _delta(base: float | None, cand: float | None, higher_better: bool = True) -> str:
    if base is None or cand is None or base == 0:
        return "N/A"
    diff = cand - base
    pct = diff / abs(base) * 100
    sign = "+" if diff >= 0 else ""
    arrow = ("↑" if diff >= 0 else "↓") if higher_better else ("↓" if diff >= 0 else "↑")
    return f"{sign}{pct:.1f}% {arrow}"


def _fmt(v: float | None, decimals: int = 3) -> str:
    if v is None:
        return "–"
    return f"{v:.{decimals}f}"


def go_no_go(base: dict, cand: dict) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    go = True

    acc_b = base.get("accuracy")
    acc_c = cand.get("accuracy")
    if acc_c is None or acc_b is None:
        reasons.append("accuracy data missing")
        go = False
    elif acc_c < acc_b:
        reasons.append(f"accuracy REGRESSION: {acc_c:.4f} < baseline {acc_b:.4f}")
        go = False

    tok_s = cand.get("tok_s")
    if tok_s is None:
        reasons.append("tok/s data missing")
        go = False
    elif tok_s < 2.5:
        reasons.append(f"tok/s too slow: {tok_s:.2f} < 2.5 (not interactive)")
        go = False

    p95 = cand.get("latency_p95_s")
    if p95 is None:
        reasons.append("latency_p95 data missing")
        go = False
    elif p95 > 15.0:
        reasons.append(f"latency_p95 too high: {p95:.1f}s > 15.0s (RPi 5 target)")
        go = False

    if go:
        reasons.append("all criteria met")
    return go, reasons


def build_markdown(base: dict, cand: dict, baseline_path: str, candidate_path: str) -> str:
    verdict, reasons = go_no_go(base, cand)
    verdict_str = "**GO ✓**" if verdict else "**NO-GO ✗**"

    cats = sorted(
        set(list(base.get("by_category", {}).keys()) + list(cand.get("by_category", {}).keys()))
    )

    lines: list[str] = [
        "# LLM Benchmark: Qwen2.5-3B vs Qwen3.5-4B (Phase 0)",
        "",
        "**Date:** 2026-05-13  ",
        "**Hardware:** RPi 5 16 GB + Hailo-8 (CV active)  ",
        f"**Baseline:** `{Path(baseline_path).name}`  ",
        f"**Candidate:** `{Path(candidate_path).name}`  ",
        "",
        f"## Verdict: {verdict_str}",
        "",
        "| Criterion | Threshold | Result |",
        "|-----------|-----------|--------|",
        f"| Accuracy | ≥ baseline | {_fmt(cand.get('accuracy'), 4)} vs {_fmt(base.get('accuracy'), 4)} {'✓' if (cand.get('accuracy') or 0) >= (base.get('accuracy') or 1) else '✗'} |",
        f"| tok/s | ≥ 2.5 | {_fmt(cand.get('tok_s'), 2)} {'✓' if (cand.get('tok_s') or 0) >= 2.5 else '✗'} |",
        f"| latency p95 | ≤ 15.0 s (RPi 5) | {_fmt(cand.get('latency_p95_s'), 2)} s {'✓' if (cand.get('latency_p95_s') or 16) <= 15.0 else '✗'} |",
        "",
        "Reasons: " + "; ".join(reasons),
        "",
        "## Full metrics",
        "",
        "| Metric | Qwen2.5-3B (baseline) | Qwen3.5-4B (candidate) | Δ |",
        "|--------|----------------------|------------------------|---|",
        f"| accuracy_overall | {_fmt(base.get('accuracy'), 4)} | {_fmt(cand.get('accuracy'), 4)} | {_delta(base.get('accuracy'), cand.get('accuracy'))} |",
        f"| json_validity | {_fmt(base.get('tool_call_validity'), 4)} | {_fmt(cand.get('tool_call_validity'), 4)} | {_delta(base.get('tool_call_validity'), cand.get('tool_call_validity'))} |",
        f"| tok/s | {_fmt(base.get('tok_s'), 2)} | {_fmt(cand.get('tok_s'), 2)} | {_delta(base.get('tok_s'), cand.get('tok_s'))} |",
        f"| latency_mean_s | {_fmt(base.get('latency_mean_s'), 2)} | {_fmt(cand.get('latency_mean_s'), 2)} | {_delta(base.get('latency_mean_s'), cand.get('latency_mean_s'), higher_better=False)} |",
        f"| latency_p95_s | {_fmt(base.get('latency_p95_s'), 2)} | {_fmt(cand.get('latency_p95_s'), 2)} | {_delta(base.get('latency_p95_s'), cand.get('latency_p95_s'), higher_better=False)} |",
        f"| RAM_GB | {_fmt(base.get('ram_gb'), 2)} | {_fmt(cand.get('ram_gb'), 2)} | {_delta(base.get('ram_gb'), cand.get('ram_gb'), higher_better=False)} |",
        f"| cv_active | {base.get('cv_active', '–')} | {cand.get('cv_active', '–')} | – |",
        f"| n_queries | {base.get('n_queries', '–')} | {cand.get('n_queries', '–')} | – |",
        "",
        "### Accuracy by category",
        "",
        "| Category | Qwen2.5-3B | Qwen3.5-4B | Δ |",
        "|----------|-----------|-----------|---|",
    ]

    for cat in cats:
        bv = base.get("by_category", {}).get(cat)
        cv = cand.get("by_category", {}).get(cat)
        lines.append(f"| {cat} | {_fmt(bv, 4)} | {_fmt(cv, 4)} | {_delta(bv, cv)} |")

    lines += [
        "",
        "## Errors",
        "",
        f"Baseline errors: {len(base.get('errors', []))}  ",
        f"Candidate errors: {len(cand.get('errors', []))}  ",
        "",
        "## MLflow run IDs",
        "",
        "| Model | Run ID |",
        "|-------|--------|",
        "| Qwen2.5-3B | _fill after bench_ |",
        "| Qwen3.5-4B | _fill after bench_ |",
        "",
        "## Notes",
        "",
        "_Fill in observations: chat_format compatibility, first-token latency, RAM pressure under CV load._",
        "",
        "---",
        "_Generated by `scripts/bench_compare.py` from:_  ",
        f"_baseline `{baseline_path}`_  ",
        f"_candidate `{candidate_path}`_",
    ]

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two LLM bench JSON results")
    parser.add_argument("--baseline", required=True, help="Path to baseline JSON")
    parser.add_argument("--candidate", required=True, help="Path to candidate JSON")
    parser.add_argument(
        "--out",
        default="materials/evaluation_results/llm_bench_comparison_2026_05.md",
        help="Output markdown path",
    )
    args = parser.parse_args()

    base_path = Path(args.baseline)
    cand_path = Path(args.candidate)

    if not base_path.exists():
        print(f"ERROR: baseline not found: {base_path}", file=sys.stderr)
        sys.exit(1)
    if not cand_path.exists():
        print(f"ERROR: candidate not found: {cand_path}", file=sys.stderr)
        sys.exit(1)

    base = json.loads(base_path.read_text())
    cand = json.loads(cand_path.read_text())

    md = build_markdown(base, cand, str(base_path), str(cand_path))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    print(f"Comparison written to {out}")
    print()
    # Print verdict to stdout for CI/CD go/no-go gate
    verdict, reasons = go_no_go(base, cand)
    print("GO" if verdict else "NO-GO", "—", "; ".join(reasons))
    sys.exit(0 if verdict else 1)


if __name__ == "__main__":
    main()
