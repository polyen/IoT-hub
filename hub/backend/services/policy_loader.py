"""Load and cache policy.yaml; expose lint and simulate helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_POLICY_PATH = Path(__file__).parents[3] / "materials" / "policy.yaml"
_cached: dict[str, Any] | None = None


def load_policy(force: bool = False) -> dict[str, Any]:
    global _cached
    if _cached is None or force:
        _cached = yaml.safe_load(_POLICY_PATH.read_text())
    return _cached


def lint_policy() -> list[dict[str, str]]:
    """Return list of {level, message} dicts for any policy issues found."""
    issues: list[dict[str, str]] = []
    try:
        policy = load_policy(force=True)
    except Exception as exc:
        return [{"level": "error", "message": f"YAML parse error: {exc}"}]

    valid_classes = {"AUTO", "CONFIRM", "DENY", "defer_to_topic"}

    for tool, cfg in (policy.get("tools") or {}).items():
        if cfg.get("class") not in valid_classes:
            issues.append(
                {"level": "error", "message": f"tools.{tool}: unknown class '{cfg.get('class')}'"}
            )

    for i, topic in enumerate(policy.get("mqtt_topics") or []):
        if topic.get("class") not in valid_classes:
            issues.append(
                {
                    "level": "error",
                    "message": f"mqtt_topics[{i}]: unknown class '{topic.get('class')}'",
                }
            )
        if not topic.get("pattern"):
            issues.append({"level": "warning", "message": f"mqtt_topics[{i}]: missing pattern"})

    for pat in (policy.get("llm") or {}).get("reject_intent_patterns") or []:
        try:
            re.compile(pat)
        except re.error as exc:
            issues.append(
                {
                    "level": "error",
                    "message": f"llm.reject_intent_patterns: invalid regex '{pat}': {exc}",
                }
            )

    return issues


def simulate(
    intent_text: str, tool: str | None = None, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Match intent/tool/payload against policy rules and return result."""
    policy = load_policy()
    llm = policy.get("llm") or {}

    # 1. reject_intent_patterns
    for pat in llm.get("reject_intent_patterns") or []:
        if re.search(pat, intent_text):
            return {
                "matched_rule": f"llm.reject_intent_patterns: {pat}",
                "class": "DENY",
                "overrides": [],
                "reason": "prompt injection pattern matched",
            }

    # 2. escalate_to_confirm_keywords
    for kw in llm.get("escalate_to_confirm_keywords") or []:
        if kw.lower() in intent_text.lower():
            return {
                "matched_rule": f"llm.escalate_to_confirm_keywords: {kw}",
                "class": "CONFIRM",
                "overrides": [],
                "reason": "escalation keyword matched",
            }

    # 3. tool-level rule
    if tool:
        tools = policy.get("tools") or {}
        if tool in tools:
            tool_cfg = tools[tool]
            cls = tool_cfg.get("class", "DENY")
            if cls == "defer_to_topic" and payload and "topic" in payload:
                return _match_mqtt_topic(payload["topic"], policy)
            return {
                "matched_rule": f"tools.{tool}",
                "class": cls,
                "overrides": [],
                "reason": "tool rule matched",
            }

        # mqtt_publish with topic
        if tool == "mqtt_publish" and payload and "topic" in payload:
            return _match_mqtt_topic(payload["topic"], policy)

    return {
        "matched_rule": "default",
        "class": policy.get("default", "DENY"),
        "overrides": [],
        "reason": "no rule matched — falling back to default",
    }


def _match_mqtt_topic(topic: str, policy: dict[str, Any]) -> dict[str, Any]:
    import fnmatch

    for rule in policy.get("mqtt_topics") or []:
        pattern = rule.get("pattern", "")
        # convert MQTT wildcards to fnmatch
        fnpat = pattern.replace("**", "*").replace("+", "*")
        if fnmatch.fnmatch(topic, fnpat):
            return {
                "matched_rule": f"mqtt_topics: {pattern}",
                "class": rule.get("class", "DENY"),
                "overrides": [],
                "reason": "mqtt topic rule matched",
            }
    return {
        "matched_rule": "mqtt_topics catch-all",
        "class": "DENY",
        "overrides": [],
        "reason": "no mqtt topic rule matched",
    }
