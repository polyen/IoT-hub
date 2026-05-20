"""LLM tool call accuracy evaluator."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


class AgentEvaluator:
    """Evaluates LLM tool call accuracy against a labeled query set."""

    def __init__(
        self,
        llm_url: str = "http://localhost:8001",
        policy_path: str = "hub/policy.yaml",
    ) -> None:
        self.llm_url = llm_url.rstrip("/")
        self.policy_path = policy_path

    def load_queries(self, queries_yaml: str) -> list[dict[str, Any]]:
        """Load queries from YAML file.

        Format: [{id, category, text, expected_tool, expected_args}]
        """
        path = Path(queries_yaml)
        if not path.exists():
            logger.warning("Queries file not found: %s", queries_yaml)
            return []
        with open(path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, list):
            raise ValueError(f"Expected a list in {queries_yaml}, got {type(data)}")
        return list(data)

    def call_llm(self, query_text: str) -> dict[str, Any]:
        """POST to OpenAI-compatible /v1/chat/completions, return parsed response."""
        if not HTTPX_AVAILABLE:
            raise RuntimeError("httpx not installed")

        payload = {
            "model": "local",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an IoT home assistant. "
                        "Respond with a JSON tool call: "
                        '{"tool": "<name>", "args": {...}}'
                    ),
                },
                {"role": "user", "content": query_text},
            ],
            "max_tokens": 256,
            "temperature": 0.0,
        }

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(f"{self.llm_url}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            return dict(resp.json())

    def _extract_tool_call(self, response: dict[str, Any]) -> dict[str, Any] | None:
        """Extract tool call dict from OpenAI-compatible response."""
        choices = response.get("choices", [])
        if not choices:
            return None

        choice = choices[0]
        message = choice.get("message", {})

        # OpenAI function/tool call format
        tool_calls = message.get("tool_calls", [])
        if tool_calls:
            tc = tool_calls[0]
            fn = tc.get("function", {})
            args_str = fn.get("arguments", "{}")
            try:
                args = json.loads(args_str)
            except json.JSONDecodeError:
                args = {}
            return {"tool": fn.get("name", ""), "args": args}

        # Plain text JSON format (llama.cpp style)
        content = message.get("content", "")
        if content:
            return self._parse_tool_call(content)

        return None

    def _parse_tool_call(self, response: str) -> dict[str, Any] | None:
        """Extract JSON tool call from plain text response."""
        import re

        # Try direct JSON parse
        text = response.strip()
        try:
            obj = json.loads(text)
            if isinstance(obj, dict) and "tool" in obj:
                return obj
        except json.JSONDecodeError:
            pass

        # Regex: find first JSON object
        match = re.search(r"\{[^{}]+\}", text, re.DOTALL)
        if match:
            try:
                obj = json.loads(match.group())
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                pass

        return None

    def check_tool_call(
        self,
        response: dict[str, Any] | None,
        expected: dict[str, Any],
    ) -> bool:
        """Check tool name matches and required args are present.

        Lenient: only checks args listed in expected_args, not exact equality.
        For 'unknown' category, accept DENY or ask_user.
        """
        expected_tool = expected.get("expected_tool", "")
        expected_args = expected.get("expected_args", {}) or {}
        category = expected.get("category", "")

        if response is None:
            return False

        actual_tool = response.get("tool", "")

        # Unknown category: accept deny/ask_user variants
        if category == "unknown":
            deny_tools = {"DENY", "deny", "ask_user", "clarify", "unknown"}
            return actual_tool in deny_tools or actual_tool == expected_tool

        if actual_tool != expected_tool:
            return False

        # Check required args (lenient)
        actual_args = response.get("args", {}) or {}
        for key, val in expected_args.items():
            if key not in actual_args:
                return False
            # String values: case-insensitive substring match
            if isinstance(val, str) and isinstance(actual_args[key], str):
                if val.lower() not in actual_args[key].lower():
                    return False
            elif actual_args[key] != val:
                # Allow type coercion mismatches
                if str(actual_args[key]) != str(val):
                    return False
        return True

    def run(self, queries_yaml: str, n_queries: int = 100) -> dict[str, Any]:
        """Evaluate queries against LLM and return accuracy metrics."""
        queries = self.load_queries(queries_yaml)
        if not queries:
            return {
                "accuracy": None,
                "note": f"No queries loaded from {queries_yaml}",
                "target": 0.90,
                "pass": None,
                "n_queries": 0,
                "errors": [],
            }

        # Check LLM reachability
        llm_reachable = False
        if HTTPX_AVAILABLE:
            try:
                with httpx.Client(timeout=5.0) as client:
                    client.get(f"{self.llm_url}/health")
                llm_reachable = True
            except Exception:
                pass

        if not llm_reachable:
            return {
                "accuracy": None,
                "note": f"LLM server not reachable at {self.llm_url}",
                "target": 0.90,
                "pass": None,
                "n_queries": len(queries[:n_queries]),
                "errors": [],
            }

        queries = queries[:n_queries]
        correct = 0
        errors: list[dict[str, Any]] = []
        by_category: dict[str, dict[str, int]] = {}

        for q in queries:
            cat = q.get("category", "unknown")
            if cat not in by_category:
                by_category[cat] = {"correct": 0, "total": 0}
            by_category[cat]["total"] += 1

            try:
                raw_response = self.call_llm(str(q.get("text", "")))
                parsed = self._extract_tool_call(raw_response)
                ok = self.check_tool_call(parsed, q)
            except Exception as exc:
                logger.warning("Query %s failed: %s", q.get("id"), exc)
                errors.append({"id": q.get("id"), "error": str(exc)})
                ok = False

            if ok:
                correct += 1
                by_category[cat]["correct"] += 1

        total = len(queries)
        accuracy = correct / total if total > 0 else 0.0

        cat_accuracy = {
            cat: round(v["correct"] / v["total"], 4) if v["total"] > 0 else 0.0
            for cat, v in by_category.items()
        }

        return {
            "accuracy": round(accuracy, 4),
            "by_category": cat_accuracy,
            "target": 0.90,
            "pass": accuracy > 0.90,
            "n_queries": total,
            "errors": errors,
        }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="LLM tool call accuracy evaluator")
    parser.add_argument("--llm-url", default="http://localhost:8001")
    parser.add_argument("--queries", default="training/llm_eval/queries.yaml")
    parser.add_argument("--n-queries", type=int, default=100)
    parser.add_argument("--output", default="materials/evaluation_results")
    args = parser.parse_args()

    evaluator = AgentEvaluator(llm_url=args.llm_url)
    result = evaluator.run(queries_yaml=args.queries, n_queries=args.n_queries)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "agent_accuracy.json"
    with open(out_file, "w") as f:
        json.dump(result, f, indent=2)

    logger.info("Results written to %s", out_file)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
