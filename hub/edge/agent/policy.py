from __future__ import annotations

import copy
import re
import signal
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml


class _StrSafeLoader(yaml.SafeLoader):  # type: ignore[misc]
    pass


_bool_tag = "tag:yaml.org,2002:bool"
_bool_pattern = re.compile(r"(?i)^(true|false)$")

_StrSafeLoader.yaml_implicit_resolvers = {
    key: [(tag, regexp) for tag, regexp in resolvers if tag != _bool_tag]
    + ([(_bool_tag, _bool_pattern)] if any(tag == _bool_tag for tag, _ in resolvers) else [])
    for key, resolvers in copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers).items()
}


class ActionClass(StrEnum):
    AUTO = "AUTO"
    CONFIRM = "CONFIRM"
    DENY = "DENY"


@dataclass
class ToolCall:
    tool: str
    topic: str | None
    payload: dict[str, Any]


@dataclass
class EvaluationContext:
    now: datetime = field(default_factory=lambda: datetime.now())
    timezone: str = "Europe/Kyiv"


@dataclass
class Decision:
    action_class: ActionClass
    reason: str
    confirm_message: str | None = None
    confirm_timeout_sec: int | None = None


class PolicyEngine:
    def __init__(self, policy_path: Path = Path("hub/policy.yaml")) -> None:
        self._policy_path = policy_path
        self._policy: dict[str, Any] = {}

    def load(self) -> None:
        with open(self._policy_path) as fh:
            self._policy = yaml.load(fh, Loader=_StrSafeLoader)

    def evaluate(
        self,
        tool_call: ToolCall,
        intent_text: str,
        identity: str = "default",
        ctx: EvaluationContext | None = None,
    ) -> Decision:
        if ctx is None:
            ctx = EvaluationContext()

        d = self._check_reject_patterns(intent_text)
        if d is not None:
            return d

        d = self._check_escalate_keywords(intent_text)
        if d is not None:
            return d

        d = self._check_schedules(tool_call, ctx)
        if d is not None:
            return d

        d = self._check_identity(tool_call, identity)
        if d is not None:
            return d

        d = self._check_tool_rule(tool_call)
        if d is not None:
            return d

        default = self._policy.get("default", "DENY")
        return Decision(action_class=ActionClass(default), reason="default_policy")

    def _check_reject_patterns(self, intent_text: str) -> Decision | None:
        patterns: list[str] = self._policy.get("llm", {}).get("reject_intent_patterns", [])
        for pattern in patterns:
            if re.search(pattern, intent_text):
                return Decision(
                    action_class=ActionClass.DENY,
                    reason="prompt_injection_attempt",
                )
        return None

    def _check_escalate_keywords(self, intent_text: str) -> Decision | None:
        keywords: list[str] = self._policy.get("llm", {}).get("escalate_to_confirm_keywords", [])
        lower = intent_text.lower()
        for kw in keywords:
            if kw.lower() in lower:
                return Decision(
                    action_class=ActionClass.CONFIRM,
                    reason=f"escalate_keyword:{kw}",
                    confirm_timeout_sec=self._policy.get("confirmation", {}).get(
                        "default_timeout_sec", 60
                    ),
                )
        return None

    def _check_schedules(self, tool_call: ToolCall, ctx: EvaluationContext) -> Decision | None:
        schedules: list[dict[str, Any]] = self._policy.get("schedules", [])
        for schedule in schedules:
            if not self._schedule_active(schedule, ctx):
                continue
            for override in schedule.get("overrides", []):
                if override.get("tool") != tool_call.tool:
                    continue
                pattern = override.get("topic_pattern")
                if pattern and tool_call.topic:
                    if not self._match_mqtt_pattern(pattern, tool_call.topic):
                        continue
                elif pattern and not tool_call.topic:
                    continue
                class_override = override.get("class_override")
                if class_override:
                    return Decision(
                        action_class=ActionClass(class_override),
                        reason=f"schedule:{schedule['name']}",
                        confirm_message=override.get("confirm_message"),
                        confirm_timeout_sec=self._policy.get("confirmation", {}).get(
                            "default_timeout_sec", 60
                        ),
                    )
        return None

    def _schedule_active(self, schedule: dict[str, Any], ctx: EvaluationContext) -> bool:
        active_hours: str | None = schedule.get("active_hours")
        if not active_hours:
            return False
        tz = ZoneInfo(ctx.timezone)
        local_now = ctx.now.astimezone(tz) if ctx.now.tzinfo else ctx.now.replace(tzinfo=tz)
        start_str, end_str = active_hours.split("-")
        sh, sm = (int(x) for x in start_str.split(":"))
        eh, em = (int(x) for x in end_str.split(":"))
        start_minutes = sh * 60 + sm
        end_minutes = eh * 60 + em
        current_minutes = local_now.hour * 60 + local_now.minute
        if start_minutes <= end_minutes:
            return start_minutes <= current_minutes < end_minutes
        # midnight wrap
        return current_minutes >= start_minutes or current_minutes < end_minutes

    def _check_identity(self, tool_call: ToolCall, identity: str) -> Decision | None:
        identities: dict[str, Any] = self._policy.get("identities", {})
        id_cfg: dict[str, Any] = identities.get(identity, {})
        blocked: list[str] = id_cfg.get("blocked_topics", [])
        topic = tool_call.topic or ""
        for pattern in blocked:
            if self._match_mqtt_pattern(pattern, topic):
                return Decision(
                    action_class=ActionClass.DENY,
                    reason=f"identity_blocked:{identity}",
                )
        return None

    def _check_tool_rule(self, tool_call: ToolCall) -> Decision | None:
        tools_cfg: dict[str, Any] = self._policy.get("tools", {})
        tool_cfg: dict[str, Any] | None = tools_cfg.get(tool_call.tool)
        if tool_cfg is not None:
            cls = tool_cfg.get("class", "DENY")
            if cls == "defer_to_topic":
                return self._check_mqtt_topic_rule(tool_call)
            return Decision(action_class=ActionClass(cls), reason=f"tool_rule:{tool_call.tool}")
        return None

    def _check_mqtt_topic_rule(self, tool_call: ToolCall) -> Decision | None:
        if not tool_call.topic:
            return Decision(action_class=ActionClass.DENY, reason="mqtt_publish_no_topic")
        mqtt_topics: list[dict[str, Any]] = self._policy.get("mqtt_topics", [])
        for rule in mqtt_topics:
            pattern: str = rule.get("pattern", "")
            if not self._match_mqtt_pattern(pattern, tool_call.topic):
                continue
            schema: dict[str, Any] | None = rule.get("schema")
            if schema is not None:
                try:
                    import jsonschema

                    jsonschema.validate(tool_call.payload, schema)
                except Exception:
                    return Decision(
                        action_class=ActionClass.DENY,
                        reason="schema_validation_failed",
                    )
            cls: str = rule.get("class", "DENY")
            return Decision(
                action_class=ActionClass(cls),
                reason=f"mqtt_topic_rule:{pattern}",
                confirm_message=rule.get("confirm_message"),
                confirm_timeout_sec=rule.get("confirm_timeout_sec"),
            )
        return Decision(action_class=ActionClass.DENY, reason="mqtt_topic_no_match")

    def _match_mqtt_pattern(self, pattern: str, topic: str) -> bool:
        if "**" in pattern:
            prefix = pattern[: pattern.index("**")]
            return topic.startswith(prefix) if prefix else True
        segments_p = pattern.split("/")
        segments_t = topic.split("/")
        i = 0
        j = 0
        while i < len(segments_p) and j < len(segments_t):
            sp = segments_p[i]
            if sp == "#":
                return True
            if sp == "+":
                i += 1
                j += 1
                continue
            if sp != segments_t[j]:
                return False
            i += 1
            j += 1
        return i == len(segments_p) and j == len(segments_t)


async def write_audit(
    decision: Decision,
    tool_call: ToolCall,
    intent_text: str,
    identity: str,
    latency_ms: int,
    llm_version: str | None = None,
) -> None:
    from hub.backend.db import AsyncSessionLocal
    from hub.backend.models import AgentAudit

    async with AsyncSessionLocal() as session:
        audit = AgentAudit(
            timestamp=datetime.now(UTC),
            intent_text=intent_text,
            tool=tool_call.tool,
            action_class=decision.action_class.value,
            executed=False,
            llm_version=llm_version,
            latency_ms=latency_ms,
        )
        session.add(audit)
        await session.commit()


engine = PolicyEngine()
try:
    engine.load()
except Exception:
    pass
signal.signal(signal.SIGHUP, lambda *_: engine.load())
