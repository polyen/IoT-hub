"""Aggregate evaluation results → JSON + Markdown report."""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_METRIC_META: dict[str, dict[str, Any]] = {
    "mAP50": {"label": "Fire/Smoke mAP@.5", "baseline": 0.32, "target": 0.78, "unit": ""},
    "F1": {"label": "Fall Detection F1", "baseline": "rule", "target": 0.80, "unit": ""},
    "fps_mean": {"label": "Cascade FPS (mean)", "baseline": 5, "target": 15, "unit": "fps"},
    "latency_mean_s": {
        "label": "STT Latency Hailo (mean)",
        "baseline": 2.5,
        "target": 0.25,
        "unit": "s",
    },
    "latency_p95_s": {
        "label": "STT Latency Hailo p95",
        "baseline": 2.8,
        "target": 0.30,
        "unit": "s",
    },
    "accuracy": {"label": "LLM Tool Accuracy", "baseline": 0.70, "target": 0.90, "unit": ""},
}


class EvaluationReport:
    """Collects evaluation sub-results and renders JSON + Markdown report."""

    def __init__(self) -> None:
        self.results: dict[str, Any] = {}

    def add(self, key: str, result: dict[str, Any]) -> None:
        """Add a named sub-result."""
        self.results[key] = result

    @staticmethod
    def find_next_version(base_dir: Path) -> int:
        """Scan results_vN.json files in base_dir and return N+1."""
        if not base_dir.exists():
            return 1
        max_ver = 0
        for p in base_dir.glob("results_v*.json"):
            m = re.search(r"results_v(\d+)\.json$", p.name)
            if m:
                ver = int(m.group(1))
                if ver > max_ver:
                    max_ver = ver
        return max_ver + 1

    def save(self, out_dir: Path) -> None:
        """Write versioned JSON and Markdown report files."""
        out_dir.mkdir(parents=True, exist_ok=True)
        ver = self.find_next_version(out_dir)

        json_path = out_dir / f"results_v{ver}.json"
        md_path = out_dir / f"results_v{ver}.md"

        with open(json_path, "w") as f:
            json.dump(self.results, f, indent=2)

        md_path.write_text(self._to_markdown())

        logger.info("Saved %s and %s", json_path, md_path)
        print(f"Report written: {json_path}")
        print(f"Markdown: {md_path}")

    def _to_markdown(self) -> str:
        """Generate Markdown table with all collected metrics."""
        lines = [
            "# IoT Hub Evaluation Report",
            "",
            "| Metric | Value | Baseline | Target | Pass |",
            "|--------|-------|----------|--------|------|",
        ]

        def _pass_str(p: Any) -> str:
            if p is True:
                return "✓"
            if p is False:
                return "✗"
            return "—"

        def _fmt(v: Any) -> str:
            if v is None:
                return "—"
            if isinstance(v, float):
                return f"{v:.4f}"
            return str(v)

        # Fire/smoke
        fs = self.results.get("cv_fire_smoke", {})
        if fs:
            map50 = fs.get("mAP50")
            lines.append(
                f"| Fire/Smoke mAP@.5 | {_fmt(map50)} | 0.32 | >0.78 | {_pass_str(fs.get('pass'))} |"
            )

        # Fall detection
        fall = self.results.get("cv_fall", {})
        if fall:
            f1 = fall.get("F1")
            lines.append(
                f"| Fall Detection F1 | {_fmt(f1)} | rule baseline | >0.80 | {_pass_str(fall.get('pass'))} |"
            )

        # Latency / FPS
        lat = self.results.get("cv_latency", {})
        if lat:
            fps = lat.get("fps_mean")
            lines.append(
                f"| Cascade FPS (mean) | {_fmt(fps)} fps | ~5 (CPU) | >15 | {_pass_str(lat.get('pass'))} |"
            )

        # STT
        stt = self.results.get("stt_latency", {})
        if stt:
            hailo = stt.get("hailo_whisper", {})
            fw = stt.get("faster_whisper", {})
            hailo_p95 = hailo.get("latency_p95_s")
            fw_mean = fw.get("latency_mean_s")
            lines.append(
                f"| Hailo Whisper p95 latency | {_fmt(hailo_p95)} s | ~2.5s (CPU) | <0.30s | {_pass_str(stt.get('pass'))} |"
            )
            lines.append(f"| faster-whisper (CPU) mean | {_fmt(fw_mean)} s | — | — | — |")
            if stt.get("speedup"):
                lines.append(f"| STT Speedup (Hailo/CPU) | {stt['speedup']}x | — | — | — |")

        # LLM accuracy
        agent = self.results.get("agent_accuracy", {})
        if agent:
            acc = agent.get("accuracy")
            lines.append(
                f"| LLM Tool Accuracy | {_fmt(acc)} | ~0.70 (vanilla ReAct) | >0.90 | {_pass_str(agent.get('pass'))} |"
            )
            by_cat = agent.get("by_category", {})
            for cat, cat_acc in by_cat.items():
                lines.append(f"| └─ {cat} | {_fmt(cat_acc)} | — | — | — |")

        lines.append("")

        # Summary
        passed = sum(
            1 for sub in self.results.values() if isinstance(sub, dict) and sub.get("pass") is True
        )
        failed = sum(
            1 for sub in self.results.values() if isinstance(sub, dict) and sub.get("pass") is False
        )
        lines.extend(
            [
                f"**Passed:** {passed} / **Failed:** {failed}",
                "",
                "*Generated by training/evaluation/report.py*",
            ]
        )

        return "\n".join(lines) + "\n"


def _load_json_if_exists(path: Path) -> dict[str, Any]:
    if path.exists():
        with open(path) as f:
            return dict(json.load(f))
    return {}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Aggregate evaluation results into report")
    parser.add_argument("--results-dir", default="materials/evaluation_results")
    parser.add_argument("--output", default="materials/evaluation_results")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    report = EvaluationReport()

    for key, filename in [
        ("cv_fire_smoke", "cv_fire_smoke.json"),
        ("cv_fall", "cv_fall.json"),
        ("cv_latency", "cv_latency.json"),
        ("stt_latency", "stt_latency.json"),
        ("agent_accuracy", "agent_accuracy.json"),
    ]:
        data = _load_json_if_exists(results_dir / filename)
        if data:
            report.add(key, data)
            logger.info("Loaded %s", filename)
        else:
            logger.warning("Not found or empty: %s", filename)

    report.save(Path(args.output))


if __name__ == "__main__":
    main()
