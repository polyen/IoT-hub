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
        """Generate Markdown table with all collected metrics.

        Sub-results carrying ``measured: false`` render as *not measured* rather
        than printing whatever placeholder value they hold — no fabricated
        number ever reaches the report.
        """
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

        def _measured(sub: dict[str, Any]) -> bool:
            # Absence of the flag = legacy result; treat as measured for back-compat.
            return sub.get("measured", True) is not False

        def _val(sub: dict[str, Any], key: str, unit: str = "") -> str:
            if not _measured(sub):
                return "_not measured_"
            v = sub.get(key)
            if v is None:
                return "—"
            if isinstance(v, float):
                return f"{v:.4f}{unit}"
            return f"{v}{unit}"

        def _row(label: str, sub: dict[str, Any], value: str, baseline: str, target: str) -> str:
            p = _pass_str(sub.get("pass")) if _measured(sub) else "—"
            return f"| {label} | {value} | {baseline} | {target} | {p} |"

        # Fire/smoke mAP
        fs = self.results.get("cv_fire_smoke", {})
        if fs:
            lines.append(_row("Fire/Smoke mAP@.5", fs, _val(fs, "mAP50"), "0.32", ">0.78"))
            if _measured(fs) and fs.get("mAP50_95") is not None:
                lines.append(f"| └─ mAP@.5-.95 | {_val(fs, 'mAP50_95')} | — | — | — |")

        # Fall detection F1
        fall = self.results.get("cv_fall", {})
        if fall:
            lines.append(_row("Fall Detection F1", fall, _val(fall, "F1"), "rule", ">0.80"))

        # Cascade FPS (CPU profiler; on-NPU FPS lives in cv_detector_compare)
        lat = self.results.get("cv_latency", {})
        if lat:
            lines.append(
                _row("Cascade FPS (mean)", lat, _val(lat, "fps_mean", " fps"), "~5 CPU", ">15")
            )

        # STT WER (primary STT quality metric — production CPU engine)
        wer = self.results.get("stt_wer", {})
        if wer:
            lines.append(_row("STT WER (UA corpus)", wer, _val(wer, "wer"), "—", "≤0.12"))
            if _measured(wer):
                lines.append(f"| └─ STT CER | {_val(wer, 'cer')} | — | — | — |")
                lines.append(
                    f"| └─ STT latency p95 | {_val(wer, 'latency_p95_s', ' s')} | — | — | — |"
                )

        # STT latency micro-benchmark (synthetic audio — compute time only)
        stt = self.results.get("stt_latency", {})
        if stt:
            fw = stt.get("faster_whisper", {})
            lines.append(
                f"| faster-whisper latency (mean) | {_val(fw, 'latency_mean_s', ' s')} | — | — | — |"
            )

        # End-to-end voice latency (NFR-2)
        e2e = self.results.get("voice_e2e_latency", {})
        if e2e:
            total = e2e.get("stages", {}).get("total", {}) if _measured(e2e) else {}
            val = "_not measured_" if not _measured(e2e) else f"{total.get('p95', '—')} s"
            lines.append(_row("Voice e2e latency (p95)", e2e, val, "—", "≤5 s"))

        # NPU contention (Contribution #3)
        npu = self.results.get("npu_contention", {})
        if npu:
            val = "_not measured_" if not _measured(npu) else f"{npu.get('degradation_pct', '—')}%"
            lines.append(f"| NPU contention (CV FPS drop w/ STT) | {val} | — | — | — |")

        # LLM accuracy
        agent = self.results.get("agent_accuracy", {})
        if agent:
            lines.append(
                _row("LLM Tool Accuracy", agent, _val(agent, "accuracy"), "~0.70", ">0.90")
            )
            if _measured(agent):
                for cat, cat_acc in agent.get("by_category", {}).items():
                    acc_str = f"{cat_acc:.4f}" if isinstance(cat_acc, float) else str(cat_acc)
                    lines.append(f"| └─ {cat} | {acc_str} | — | — | — |")

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
        ("stt_wer", "stt_wer.json"),
        ("stt_latency", "stt_latency.json"),
        ("voice_e2e_latency", "voice_e2e_latency.json"),
        ("npu_contention", "npu_contention.json"),
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
