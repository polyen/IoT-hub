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


_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")


def _detect_lang(text: str) -> str:
    """Heuristic language tag for per-language metrics. Cyrillic → 'ua', else → 'en'."""
    return "ua" if _CYRILLIC_RE.search(text) else "en"


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
        r"""Extract JSON tool call from response text.

        Handles nested JSON (e.g. {"tool": "x", "args": {"topic": "..."}})
        by scanning for the outermost balanced {…} rather than using a
        no-nesting regex.  The old regex r"\{[^{}]+\}" matched inner args
        dicts instead of the outer object, causing accuracy=0 even when the
        model returned correct JSON.
        """
        text = response.strip()
        # Direct JSON parse — works when model returns only JSON
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

        # Walk the string to find the outermost balanced {…} object.
        # This handles nested braces that the old r"\{[^{}]+\}" regex missed.
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        in_string = False
        escape_next = False
        for i, ch in enumerate(text[start:], start):
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        pass
                    # Outer object found but unparseable — don't try further
                    return None
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

    @staticmethod
    def _summarize_lang_buckets(
        buckets: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, float | int]]:
        """Reduce per-language raw buckets to summary dict for output JSON."""
        out: dict[str, dict[str, float | int]] = {}
        for lang, b in buckets.items():
            total = int(b.get("total", 0) or 0)
            if total == 0:
                continue
            correct = int(b.get("correct", 0) or 0)
            valid = int(b.get("valid_json", 0) or 0)
            lat_list: list[float] = list(b.get("latencies") or [])
            tok_list: list[float] = list(b.get("tok_s") or [])
            out[lang] = {
                "n": total,
                "accuracy": round(correct / total, 4),
                "tool_call_validity": round(valid / total, 4),
                "latency_mean_s": round(statistics.mean(lat_list), 3) if lat_list else 0.0,
                "tok_s": round(statistics.mean(tok_list), 2) if tok_list else 0.0,
            }
        return out

    def run_phase_a(self, queries: list[dict[str, Any]]) -> dict[str, Any]:
        """Phase A: accuracy, tool_call_validity, latency_mean, RAM."""
        if not HTTPX_AVAILABLE:
            logger.warning("httpx not available — returning stub Phase A result")
            return self._stub_phase_a()

        correct = 0
        valid_json = 0
        latencies: list[float] = []
        by_category: dict[str, dict[str, int]] = {}
        by_language: dict[str, dict[str, Any]] = {}
        errors: list[dict[str, Any]] = []
        ram_samples: list[float] = []

        for q in queries:
            cat = q.get("category", "unknown")
            if cat not in by_category:
                by_category[cat] = {"correct": 0, "total": 0}
            by_category[cat]["total"] += 1

            text = str(q.get("text", ""))
            lang = _detect_lang(text)
            lang_bucket = by_language.setdefault(
                lang,
                {"total": 0, "correct": 0, "valid_json": 0, "latencies": [], "tok_s": []},
            )
            lang_bucket["total"] += 1

            run_latencies: list[float] = []
            last_parsed: dict[str, Any] | None = None
            success = False

            for run_idx in range(self.config.n_runs):
                try:
                    content, lat = self.query_model(text)
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
                lang_bucket["valid_json"] += 1
            if success:
                correct += 1
                by_category[cat]["correct"] += 1
                lang_bucket["correct"] += 1
            if run_latencies:
                mean_lat = statistics.mean(run_latencies)
                latencies.append(mean_lat)
                lang_bucket["latencies"].append(mean_lat)
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
            "by_language": self._summarize_lang_buckets(by_language),
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
        by_language: dict[str, dict[str, Any]] = {}
        errors: list[dict[str, Any]] = []
        ram_samples: list[float] = []

        for q in queries:
            cat = q.get("category", "unknown")
            if cat not in by_category:
                by_category[cat] = {"correct": 0, "total": 0}
            by_category[cat]["total"] += 1

            text = str(q.get("text", ""))
            lang = _detect_lang(text)
            lang_bucket = by_language.setdefault(
                lang,
                {"total": 0, "correct": 0, "valid_json": 0, "latencies": [], "tok_s": []},
            )
            lang_bucket["total"] += 1

            run_latencies: list[float] = []
            run_tok_s: list[float] = []
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
                            {"role": "user", "content": text},
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
                        run_tok_s.append(tok_s)
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
                lang_bucket["valid_json"] += 1
            if success:
                correct += 1
                by_category[cat]["correct"] += 1
                lang_bucket["correct"] += 1
            if run_latencies:
                mean_lat = statistics.mean(run_latencies)
                latencies.append(mean_lat)
                lang_bucket["latencies"].append(mean_lat)
            if run_tok_s:
                lang_bucket["tok_s"].append(statistics.mean(run_tok_s))
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
            "by_language": self._summarize_lang_buckets(by_language),
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


def _filter_queries_by_lang(queries: list[dict[str, Any]], lang: str) -> list[dict[str, Any]]:
    if lang == "all":
        return queries
    return [q for q in queries if _detect_lang(str(q.get("text", ""))) == lang]


def aggregate_results(results_dir: Path, out_md: Path) -> dict[str, Any]:
    """Scan a directory for llm_bench_*.json files and produce a side-by-side matrix.

    Output:
      - {out_md}        — Markdown comparison table for the thesis Results chapter.
      - {out_md}.json   — Same data as machine-readable JSON (sibling file).
    """
    files = sorted(results_dir.rglob("llm_bench_*.json"))
    # Exclude any pre-existing aggregate matrix to avoid recursive inclusion.
    files = [f for f in files if not f.name.startswith("llm_bench_matrix")]

    rows: list[dict[str, Any]] = []
    for f in files:
        try:
            with open(f) as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Skip %s: %s", f, exc)
            continue
        if not isinstance(data, dict) or data.get("accuracy") is None:
            continue
        by_lang = data.get("by_language", {}) or {}
        ua = by_lang.get("ua", {})
        en = by_lang.get("en", {})
        rows.append(
            {
                "model": data.get("model", f.stem.replace("llm_bench_", "")),
                "phase": data.get("phase", "?"),
                "cv_active": data.get("cv_active", False),
                "accuracy": data.get("accuracy"),
                "tool_call_validity": data.get("tool_call_validity"),
                "tok_s": data.get("tok_s"),
                "latency_mean_s": data.get("latency_mean_s"),
                "latency_p95_s": data.get("latency_p95_s"),
                "ram_gb": data.get("ram_gb"),
                "ua_accuracy": ua.get("accuracy"),
                "ua_tok_s": ua.get("tok_s"),
                "en_accuracy": en.get("accuracy"),
                "en_tok_s": en.get("tok_s"),
                "n_queries": data.get("n_queries"),
                "source": (
                    str(f.relative_to(results_dir)) if f.is_relative_to(results_dir) else str(f)
                ),
            }
        )

    rows.sort(key=lambda r: (-(r.get("accuracy") or 0.0), -(r.get("tok_s") or 0.0)))

    out_md.parent.mkdir(parents=True, exist_ok=True)

    def _fmt(v: Any, suffix: str = "") -> str:
        if v is None:
            return "—"
        if isinstance(v, bool):
            return "✓" if v else "—"
        return f"{v}{suffix}"

    header = (
        "| Model | Phase | CV | Acc | Valid | tok/s | lat mean (s) | lat p95 (s) "
        "| RAM (GB) | UA acc | UA tok/s | EN acc | EN tok/s | n |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    body_lines = []
    for r in rows:
        body_lines.append(
            "| {model} | {phase} | {cv} | {acc} | {valid} | {tok} | {lat} | {p95} "
            "| {ram} | {ua_acc} | {ua_tok} | {en_acc} | {en_tok} | {n} |".format(
                model=r["model"],
                phase=r["phase"],
                cv=_fmt(r["cv_active"]),
                acc=_fmt(r["accuracy"]),
                valid=_fmt(r["tool_call_validity"]),
                tok=_fmt(r["tok_s"]),
                lat=_fmt(r["latency_mean_s"]),
                p95=_fmt(r["latency_p95_s"]),
                ram=_fmt(r["ram_gb"]),
                ua_acc=_fmt(r["ua_accuracy"]),
                ua_tok=_fmt(r["ua_tok_s"]),
                en_acc=_fmt(r["en_accuracy"]),
                en_tok=_fmt(r["en_tok_s"]),
                n=_fmt(r["n_queries"]),
            )
        )

    out_md.write_text(
        "# LLM bench matrix\n\n"
        f"Aggregated from `{results_dir}` ({len(rows)} model runs).\n\n"
        "Sorted by accuracy desc, then tok/s desc. `—` = not reported (e.g. Phase A skips tok/s).\n\n"
        + header
        + "\n".join(body_lines)
        + "\n"
    )
    out_json = out_md.with_suffix(".json")
    out_json.write_text(json.dumps({"rows": rows}, indent=2))
    logger.info("Aggregate written to %s (%d rows) + %s", out_md, len(rows), out_json.name)
    return {"rows": rows, "n_models": len(rows)}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="LLM tool accuracy benchmark")
    parser.add_argument(
        "--mode",
        default="bench",
        choices=["bench", "aggregate"],
        help="bench: run a single-model benchmark (default). aggregate: scan llm_bench_*.json files and emit a comparison matrix.",
    )
    parser.add_argument("--llm-url", default="http://localhost:8001")
    parser.add_argument("--model-name", default="qwen3.5-4b-q4")
    parser.add_argument("--phase", default="A", choices=["A", "B"])
    parser.add_argument("--queries", default="training/llm_eval/queries.yaml")
    parser.add_argument(
        "--lang",
        default="all",
        choices=["all", "ua", "en"],
        help="Filter queries to a single language before benchmarking (heuristic on Cyrillic).",
    )
    parser.add_argument("--cv-active", action="store_true", help="Phase B: CV pipeline active")
    parser.add_argument("--output", default="materials/evaluation_results")
    parser.add_argument(
        "--matrix-out",
        default="materials/evaluation_results/llm_bench_matrix.md",
        help="aggregate mode: path for the comparison Markdown table.",
    )
    parser.add_argument("--constrained", action="store_true", default=True)
    parser.add_argument("--no-constrained", dest="constrained", action="store_false")
    parser.add_argument("--n-runs", type=int, default=3)
    args = parser.parse_args()

    if args.mode == "aggregate":
        aggregate_results(Path(args.output), Path(args.matrix_out))
        return

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

    queries = _filter_queries_by_lang(queries, args.lang)
    if args.lang != "all":
        logger.info("Filtered to lang=%s: %d queries", args.lang, len(queries))

    if args.phase == "A":
        result = bench.run_phase_a(queries)
    else:
        result = bench.run_phase_b(queries, cv_active=args.cv_active)

    result["lang_filter"] = args.lang

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^\w.-]", "_", args.model_name)
    lang_suffix = "" if args.lang == "all" else f"_{args.lang}"
    out_file = out_dir / f"llm_bench_{safe_name}{lang_suffix}.json"
    with open(out_file, "w") as f:
        json.dump(result, f, indent=2)

    logger.info("Results written to %s", out_file)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
