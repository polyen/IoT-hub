"""Load and cache policy.yaml; expose lint and simulate helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_POLICY_PATH = Path(__file__).parents[3] / "hub" / "policy.yaml"
_cached: dict[str, Any] | None = None

# Intent keyword patterns → (tool, sample_mqtt_topic | None).
# First match wins; order matters — more specific patterns come first.
_INTENT_PATTERNS: list[tuple[str, str, str | None]] = [
    (r"(?i)(замок|двер|відчин|розблокуй|lock|unlock|door)", "mqtt_publish", "home/room/lock/cmd"),
    (
        r"(?i)(охорон|сигналіз|безпек|arm|disarm|alarm|security)",
        "mqtt_publish",
        "home/security/cmd",
    ),
    (
        r"(?i)(температ|клімат|кондиц|обігрів|heat|cool|climate|thermostat)",
        "mqtt_publish",
        "home/room/climate/cmd",
    ),
    (
        r"(?i)(музик|фільм|медіа|плей|зупин|гучніст|volume|music|film|media|play|pause)",
        "mqtt_publish",
        "home/room/media/cmd",
    ),
    (r"(?i)(світл|лампу?|люстр|підсвіт|light|lamp|bulb)", "mqtt_publish", "home/room/light"),
    (r"(?i)(вентилятор|чайник|реле|relay|fan|kettle|switch)", "mqtt_publish", "home/room/cmd"),
    (r"(?i)(таймер|нагадай|нагадування|timer|remind)", "set_timer", None),
    (r"(?i)(дайджест|підсумок|що відбул|summary|digest)", "summarize_period", None),
    (r"(?i)(повідом|нотиф|надішли|push|notify|alert)", "send_push", None),
    (r"(?i)(стан|статус|покажи|перевір|стани|state|status|show|check)", "get_home_state", None),
]


def _infer_tool(intent_text: str) -> tuple[str, str | None] | None:
    """Return (tool, sample_topic) inferred from intent text, or None."""
    for pattern, tool, topic in _INTENT_PATTERNS:
        if re.search(pattern, intent_text):
            return tool, topic
    return None


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
                "inferred_tool": None,
            }

    # 2. escalate_to_confirm_keywords
    for kw in llm.get("escalate_to_confirm_keywords") or []:
        if kw.lower() in intent_text.lower():
            return {
                "matched_rule": f"llm.escalate_to_confirm_keywords: {kw}",
                "class": "CONFIRM",
                "overrides": [],
                "reason": "escalation keyword matched",
                "inferred_tool": None,
            }

    # 3. When no tool given, infer one from intent text.
    inferred_tool: str | None = None
    if not tool:
        inferred = _infer_tool(intent_text)
        if inferred:
            tool, inferred_topic = inferred
            inferred_tool = tool
            if payload is None and inferred_topic:
                payload = {"topic": inferred_topic}

    # 4. tool-level rule
    if tool:
        tools = policy.get("tools") or {}
        if tool in tools:
            tool_cfg = tools[tool]
            cls = tool_cfg.get("class", "DENY")
            if cls == "defer_to_topic" and payload and "topic" in payload:
                result = _match_mqtt_topic(payload["topic"], policy)
                result["inferred_tool"] = inferred_tool
                return result
            return {
                "matched_rule": f"tools.{tool}",
                "class": cls,
                "overrides": [],
                "reason": "tool rule matched",
                "inferred_tool": inferred_tool,
            }

        # mqtt_publish with topic
        if tool == "mqtt_publish" and payload and "topic" in payload:
            result = _match_mqtt_topic(payload["topic"], policy)
            result["inferred_tool"] = inferred_tool
            return result

    return {
        "matched_rule": "default",
        "class": policy.get("default", "DENY"),
        "overrides": [],
        "reason": "no rule matched — falling back to default",
        "inferred_tool": None,
    }


def _mqtt_pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Convert MQTT topic pattern to a compiled regex.

    MQTT semantics: ``+`` matches exactly one level (no ``/``),
    ``#`` or ``**`` matches the rest of the topic (any levels).
    """
    parts = pattern.split("/")
    regex_parts: list[str] = []
    for part in parts:
        if part in ("#", "**"):
            # Consume this level and everything after it, then stop.
            regex_parts.append(".*")
            break
        elif part == "*":
            regex_parts.append("[^/]*")
        elif part == "+":
            regex_parts.append("[^/]+")
        else:
            regex_parts.append(re.escape(part))
    return re.compile(r"\A" + r"/".join(regex_parts) + r"\Z")


def _match_mqtt_topic(topic: str, policy: dict[str, Any]) -> dict[str, Any]:
    for rule in policy.get("mqtt_topics") or []:
        pattern = rule.get("pattern", "")
        try:
            rx = _mqtt_pattern_to_regex(pattern)
        except re.error:
            continue
        if rx.match(topic):
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
