"""LLM tool accuracy benchmark — Phase A (Mac filter) and Phase B (RPi5 final)."""

from __future__ import annotations

import argparse
import json
import logging
import re
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


@dataclass
class BenchConfig:
    llm_url: str
    model_name: str
    phase: str = "A"
    n_runs: int = 3
    timeout_s: int = 30
    constrained: bool = True


class LLMBench:
    """Benchmarks LLM tool-calling accuracy, latency, and RAM usage."""

    def __init__(self, config: BenchConfig) -> None:
        self.config = config

    def query_model(
        self,
        prompt: str,
        grammar: str | None = None,
    ) -> tuple[str, float]:
        """POST to /v1/chat/completions, return (response_text, latency_s)."""
        if not HTTPX_AVAILABLE:
            raise RuntimeError("httpx not installed — pip install httpx")

        payload: dict[str, Any] = {
            "model": self.config.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a home IoT assistant. "
                        "Always respond with a single JSON object: "
                        '{"tool": "<tool_name>", "args": {...}}'
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 256,
            "temperature": 0.0,
        }

        if grammar is not None and self.config.constrained:
            payload["grammar"] = grammar

        t0 = time.perf_counter()
        with httpx.Client(timeout=float(self.config.timeout_s)) as client:
            resp = client.post(
                f"{self.config.llm_url.rstrip('/')}/v1/chat/completions",
                json=payload,
            )
            resp.raise_for_status()
            body = resp.json()
        latency_s = time.perf_counter() - t0

        choices = body.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "")
        else:
            content = ""

        return content, latency_s

    def parse_tool_call(self, response: str) -> dict[str, Any] | None:
        """Extract JSON tool call from response text."""
        text = response.strip()
        # Direct JSON parse
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

        # Regex: find first JSON-like object
        match = re.search(r"\{[^{}]+\}", text, re.DOTALL)
        if match:
            try:
                obj = json.loads(match.group())
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                pass

        return None

    def check_accuracy(
        self,
        parsed: dict[str, Any] | None,
        expected: dict[str, Any],
    ) -> bool:
        """Check tool name + key args match expected."""
        if parsed is None:
            return False

        expected_tool = expected.get("expected_tool", "")
        expected_args = expected.get("expected_args", {}) or {}
        category = expected.get("category", "")

        actual_tool = parsed.get("tool", "")

        if category == "unknown":
            deny_set = {"DENY", "deny", "ask_user", "clarify", "unknown"}
            return actual_tool in deny_set or actual_tool == expected_tool

        if actual_tool != expected_tool:
            return False

        actual_args = parsed.get("args", {}) or {}
        for key, val in expected_args.items():
            if key not in actual_args:
                return False
            if isinstance(val, str) and isinstance(actual_args[key], str):
                if val.lower() not in actual_args[key].lower():
                    return False
            elif str(actual_args.get(key)) != str(val):
                return False
        return True

    def measure_ram(self) -> float:
        """Return current RAM usage in GB."""
        if PSUTIL_AVAILABLE:
            used: int = psutil.virtual_memory().used
            return used / 1e9

        # Fallback: read /proc/meminfo (Linux only)
        try:
            meminfo = Path("/proc/meminfo").read_text()
            for line in meminfo.splitlines():
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    total_line = next(
                        ln for ln in meminfo.splitlines() if ln.startswith("MemTotal:")
                    )
                    total_kb = int(total_line.split()[1])
                    used_kb = total_kb - kb
                    return used_kb / 1e6
        except (OSError, StopIteration, ValueError):
            pass

        return 0.0

    def _compute_tok_s(self, response_body: dict[str, Any], latency_s: float) -> float:
        """Compute tokens per second from usage field."""
        usage = response_body.get("usage", {})
        completion_tokens = int(usage.get("completion_tokens", 0))
        if latency_s > 0 and completion_tokens > 0:
            return float(round(completion_tokens / latency_s, 2))
        return 0.0

    def _load_queries(self, queries_path: str) -> list[dict[str, Any]]:
        path = Path(queries_path)
        if not path.exists():
            logger.error("Queries file not found: %s", queries_path)
            return []
        with open(path) as f:
            data = yaml.safe_load(f)
        return list(data) if isinstance(data, list) else []

    def run_phase_a(self, queries: list[dict[str, Any]]) -> dict[str, Any]:
        """Phase A: accuracy, tool_call_validity, latency_mean, RAM."""
        if not HTTPX_AVAILABLE:
            logger.warning("httpx not available — returning stub Phase A result")
            return self._stub_phase_a()

        correct = 0
        valid_json = 0
        latencies: list[float] = []
        by_category: dict[str, dict[str, int]] = {}
        errors: list[dict[str, Any]] = []
        ram_samples: list[float] = []

        for q in queries:
            cat = q.get("category", "unknown")
            if cat not in by_category:
                by_category[cat] = {"correct": 0, "total": 0}
            by_category[cat]["total"] += 1

            run_latencies: list[float] = []
            last_parsed: dict[str, Any] | None = None
            success = False

            for run_idx in range(self.config.n_runs):
                try:
                    content, lat = self.query_model(str(q.get("text", "")))
                    run_latencies.append(lat)
                    parsed = self.parse_tool_call(content)
                    if parsed is not None:
                        last_parsed = parsed
                    if run_idx == 0:
                        success = self.check_accuracy(parsed, q)
                except Exception as exc:
                    logger.warning("Query %s run %d failed: %s", q.get("id"), run_idx, exc)
                    errors.append({"id": q.get("id"), "error": str(exc)})

            if last_parsed is not None:
                valid_json += 1
            if success:
                correct += 1
                by_category[cat]["correct"] += 1
            if run_latencies:
                latencies.append(statistics.mean(run_latencies))
            ram_samples.append(self.measure_ram())

        total = len(queries)
        accuracy = correct / total if total > 0 else 0.0
        validity = valid_json / total if total > 0 else 0.0
        lat_mean = statistics.mean(latencies) if latencies else 0.0
        ram_gb = statistics.mean(ram_samples) if ram_samples else 0.0

        cat_accuracy = {
            cat: round(v["correct"] / v["total"], 4) if v["total"] > 0 else 0.0
            for cat, v in by_category.items()
        }

        return {
            "phase": "A",
            "model": self.config.model_name,
            "accuracy": round(accuracy, 4),
            "tool_call_validity": round(validity, 4),
            "latency_mean_s": round(lat_mean, 3),
            "ram_gb": round(ram_gb, 2),
            "by_category": cat_accuracy,
            "n_queries": total,
            "errors": errors,
        }

    def _stub_phase_a(self) -> dict[str, Any]:
        return {
            "phase": "A",
            "model": self.config.model_name,
            "accuracy": None,
            "tool_call_validity": None,
            "latency_mean_s": None,
            "ram_gb": None,
            "by_category": {},
            "n_queries": 0,
            "note": f"LLM server not reachable at {self.config.llm_url}",
        }

    def run_phase_b(
        self,
        queries: list[dict[str, Any]],
        cv_active: bool = False,
    ) -> dict[str, Any]:
        """Phase B: Phase A metrics + tok/s, latency_p95, CV-active condition."""
        if not HTTPX_AVAILABLE:
            return self._stub_phase_b(cv_active)

        correct = 0
        valid_json = 0
        latencies: list[float] = []
        tok_s_list: list[float] = []
        by_category: dict[str, dict[str, int]] = {}
        errors: list[dict[str, Any]] = []
        ram_samples: list[float] = []

        for q in queries:
            cat = q.get("category", "unknown")
            if cat not in by_category:
                by_category[cat] = {"correct": 0, "total": 0}
            by_category[cat]["total"] += 1

            run_latencies: list[float] = []
            last_parsed: dict[str, Any] | None = None
            success = False

            for run_idx in range(self.config.n_runs):
                try:
                    payload: dict[str, Any] = {
                        "model": self.config.model_name,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "You are a home IoT assistant. "
                                    "Always respond with JSON: "
                                    '{"tool": "<name>", "args": {...}}'
                                ),
                            },
                            {"role": "user", "content": str(q.get("text", ""))},
                        ],
                        "max_tokens": 256,
                        "temperature": 0.0,
                    }
                    t0 = time.perf_counter()
                    with httpx.Client(timeout=float(self.config.timeout_s)) as client:
                        resp = client.post(
                            f"{self.config.llm_url.rstrip('/')}/v1/chat/completions",
                            json=payload,
                        )
                        resp.raise_for_status()
                        body = resp.json()
                    lat = time.perf_counter() - t0

                    choices = body.get("choices", [])
                    content = choices[0].get("message", {}).get("content", "") if choices else ""
                    run_latencies.append(lat)
                    tok_s = self._compute_tok_s(body, lat)
                    if tok_s > 0:
                        tok_s_list.append(tok_s)
                    parsed = self.parse_tool_call(content)
                    if parsed is not None:
                        last_parsed = parsed
                    if run_idx == 0:
                        success = self.check_accuracy(parsed, q)
                except Exception as exc:
                    logger.warning("Query %s run %d failed: %s", q.get("id"), run_idx, exc)
                    errors.append({"id": q.get("id"), "error": str(exc)})

            if last_parsed is not None:
                valid_json += 1
            if success:
                correct += 1
                by_category[cat]["correct"] += 1
            if run_latencies:
                latencies.append(statistics.mean(run_latencies))
            ram_samples.append(self.measure_ram())

        total = len(queries)
        accuracy = correct / total if total > 0 else 0.0
        validity = valid_json / total if total > 0 else 0.0
        lat_mean = statistics.mean(latencies) if latencies else 0.0
        lat_p95 = 0.0
        if latencies:
            lat_sorted = sorted(latencies)
            p95_idx = max(0, int(len(lat_sorted) * 0.95) - 1)
            lat_p95 = lat_sorted[p95_idx]
        tok_s_mean = statistics.mean(tok_s_list) if tok_s_list else 0.0
        ram_gb = statistics.mean(ram_samples) if ram_samples else 0.0

        cat_accuracy = {
            cat: round(v["correct"] / v["total"], 4) if v["total"] > 0 else 0.0
            for cat, v in by_category.items()
        }

        return {
            "phase": "B",
            "model": self.config.model_name,
            "cv_active": cv_active,
            "accuracy": round(accuracy, 4),
            "tool_call_validity": round(validity, 4),
            "latency_mean_s": round(lat_mean, 3),
            "latency_p95_s": round(lat_p95, 3),
            "tok_s": round(tok_s_mean, 2),
            "ram_gb": round(ram_gb, 2),
            "by_category": cat_accuracy,
            "n_queries": total,
            "errors": errors,
        }

    def _stub_phase_b(self, cv_active: bool) -> dict[str, Any]:
        return {
            "phase": "B",
            "model": self.config.model_name,
            "cv_active": cv_active,
            "accuracy": None,
            "tool_call_validity": None,
            "latency_mean_s": None,
            "latency_p95_s": None,
            "tok_s": None,
            "ram_gb": None,
            "by_category": {},
            "n_queries": 0,
            "note": f"LLM server not reachable at {self.config.llm_url}",
        }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="LLM tool accuracy benchmark")
    parser.add_argument("--llm-url", default="http://localhost:8001")
    parser.add_argument("--model-name", default="qwen3.5-4b-q4")
    parser.add_argument("--phase", default="A", choices=["A", "B"])
    parser.add_argument("--queries", default="training/llm_eval/queries.yaml")
    parser.add_argument("--cv-active", action="store_true", help="Phase B: CV pipeline active")
    parser.add_argument("--output", default="materials/evaluation_results")
    parser.add_argument("--constrained", action="store_true", default=True)
    parser.add_argument("--no-constrained", dest="constrained", action="store_false")
    parser.add_argument("--n-runs", type=int, default=3)
    args = parser.parse_args()

    config = BenchConfig(
        llm_url=args.llm_url,
        model_name=args.model_name,
        phase=args.phase,
        n_runs=args.n_runs,
        constrained=args.constrained,
    )
    bench = LLMBench(config)

    queries_path = Path(args.queries)
    if not queries_path.exists():
        logger.error("Queries file not found: %s", queries_path)
        return

    with open(queries_path) as f:
        queries: list[dict[str, Any]] = yaml.safe_load(f)

    if args.phase == "A":
        result = bench.run_phase_a(queries)
    else:
        result = bench.run_phase_b(queries, cv_active=args.cv_active)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^\w.-]", "_", args.model_name)
    out_file = out_dir / f"llm_bench_{safe_name}.json"
    with open(out_file, "w") as f:
        json.dump(result, f, indent=2)

    logger.info("Results written to %s", out_file)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
