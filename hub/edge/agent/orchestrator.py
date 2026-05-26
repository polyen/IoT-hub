"""Agent orchestrator — the main event loop for the LLM agent.

Pipeline per command:
  1. Receive text from voice/command MQTT
  2. IntentRouter.classify_intent() → IntentClass
  3. Route:
     - DETERMINISTIC → direct tool lookup + PolicyEngine → execute
     - STRUCTURED    → LocalLLMClient.generate_constrained() → PolicyEngine → execute
     - CREATIVE      → LocalLLMClient.generate() → tool parse → PolicyEngine → execute
     - UNKNOWN       → ask_user tool
  4. PolicyEngine: AUTO → execute | CONFIRM → push + wait | DENY → log
  5. Write AgentAudit row
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import aiomqtt
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from hub.backend.config import settings
from hub.edge.agent import tools as agent_tools
from hub.edge.agent.grammars import load_grammar
from hub.edge.agent.llm_local import LocalLLMClient
from hub.edge.agent.policy import (
    ActionClass,
    Decision,
    EvaluationContext,
    PolicyEngine,
    ToolCall,
    write_audit,
)
from hub.edge.agent.router import IntentClass, IntentRouter

# How long to wait for user confirmation before auto-denying
_CONFIRM_DEFAULT_TIMEOUT_SEC = 60

logger = logging.getLogger(__name__)

# Deterministic routing table: intent prototype label → tool name
DETERMINISTIC_MAP: dict[str, str] = {
    "light_on": "mqtt_publish",
    "light_off": "mqtt_publish",
    "device_off": "mqtt_publish",
    "relay_toggle": "mqtt_publish",
    "timer_set": "set_timer",
}

# STRUCTURED prototype → GBNF grammar name
GRAMMAR_MAP: dict[str, str] = {
    "light_on": "light",
    "light_off": "light",
    "relay_toggle": "relay",
    "device_off": "relay",
    "timer_set": "timer",
}

# System prompt for CREATIVE branch — tells the LLM which tools it can call
_CREATIVE_SYSTEM = """You are a smart home assistant. Choose ONE tool and respond with valid JSON only.

Available tools:
{tool_schemas}

User command: {text}

Respond ONLY with a JSON object like: {{"tool": "<name>", "payload": {{...}}}}
Pick the most relevant tool. Use summarize_period for summary/report requests, query_events_db for specific event lookups, get_home_state for current state, send_push for alerts, ask_user if unclear.
<think>

</think>
JSON:"""

_TOOL_SCHEMA_SUMMARY = "\n".join(
    f"- {name}: {list(schema.get('properties', {}).keys())}"
    for name, schema in agent_tools.TOOL_SCHEMAS.items()
)


class AgentOrchestrator:
    def __init__(
        self,
        policy: PolicyEngine,
        router: IntentRouter,
        llm: LocalLLMClient,
        redis_client: aioredis.Redis,
        mqtt_client: aiomqtt.Client,
        session_factory: Any | None = None,
        max_tool_calls_per_turn: int = 5,
    ) -> None:
        self._policy = policy
        self._router = router
        self._llm = llm
        self._redis = redis_client
        self._mqtt = mqtt_client
        self._session_factory = session_factory
        self._max_tool_calls = max_tool_calls_per_turn

    @asynccontextmanager
    async def _get_session(self) -> AsyncGenerator[AsyncSession | None, None]:
        if self._session_factory is None:
            yield None
            return
        async with self._session_factory() as session:
            yield session

    async def _pub(self, channel: str, payload: dict[str, Any]) -> None:
        """Publish a progress event to Redis with timestamp; non-fatal on error."""
        try:
            payload.setdefault("ts", datetime.now(UTC).isoformat())
            await self._redis.publish(channel, json.dumps(payload))
        except Exception:
            pass

    async def handle_command(self, text: str, identity: str = "default") -> None:
        """Process one voice/text command end-to-end."""
        t0 = time.monotonic()
        intent = self._router.classify_intent(text)
        logger.info("Intent: %s (score=%.2f) — %r", intent.class_, intent.score, text)

        routing = intent.class_.value if hasattr(intent.class_, "value") else str(intent.class_)
        await self._pub(
            "agent:turn",
            {
                "type": "intent",
                "text": text,
                "class_": routing,
                "score": round(float(intent.score), 3) if intent.score is not None else None,
                "prototype": intent.prototype,
            },
        )

        try:
            if intent.class_ == IntentClass.DETERMINISTIC:
                await self._handle_deterministic(text, identity, intent)
            elif intent.class_ == IntentClass.STRUCTURED:
                await self._handle_structured(text, identity, intent)
            elif intent.class_ == IntentClass.CREATIVE:
                await self._handle_creative(text, identity)
            else:
                await self._handle_unknown(text, identity)
        except Exception:
            logger.exception("Orchestrator error handling command: %r", text)
            await self._pub(
                "agent:result",
                {"type": "result", "action_class": "ERROR", "text": f"Помилка обробки: {text!r}"},
            )
        finally:
            latency_ms = int((time.monotonic() - t0) * 1000)
            logger.info("Command handled in %dms", latency_ms)

    async def _handle_deterministic(self, text: str, identity: str, intent: Any) -> None:
        """Route via DETERMINISTIC_MAP, skip LLM entirely."""
        tool_name = DETERMINISTIC_MAP.get(intent.prototype or "", "ask_user")
        tool_call = ToolCall(tool=tool_name, topic=None, payload={"text": text})
        ctx = EvaluationContext()
        decision = self._policy.evaluate(tool_call, text, identity, ctx)
        await self._execute_decision(decision, tool_call, text, identity)

    async def _handle_structured(self, text: str, identity: str, intent: Any = None) -> None:
        """Use constrained LLM generation to produce tool call JSON."""
        prototype = getattr(intent, "prototype", None) if intent else None
        grammar_name = GRAMMAR_MAP.get(prototype or "", None)
        if grammar_name is None:
            lower = text.lower()
            if any(kw in lower for kw in ("таймер", "нагадай", "timer", "remind")):
                grammar_name = "timer"
            elif any(kw in lower for kw in ("relay", "реле", "device_off", "розетк")):
                grammar_name = "relay"
            else:
                grammar_name = "light"

        grammar = load_grammar(grammar_name)
        prompt = f"Convert to smart home JSON tool call: {text}\nJSON:"
        try:
            result = await self._llm.generate_constrained(prompt, grammar)
        except Exception as e:
            logger.warning("Constrained generation failed: %s — falling back to ask_user", e)
            result = {"tool": "ask_user", "question": "Не зрозумів команду."}

        tool_name = str(result.get("tool", "mqtt_publish"))
        topic = result.get("topic")
        payload = result.get("payload", result)
        tool_call = ToolCall(tool=tool_name, topic=topic, payload=payload)
        decision = self._policy.evaluate(tool_call, text, identity)
        await self._execute_decision(decision, tool_call, text, identity)

    async def _handle_creative(self, text: str, identity: str) -> None:
        """Use unconstrained LLM to select and parameterize a tool."""
        prompt = _CREATIVE_SYSTEM.format(tool_schemas=_TOOL_SCHEMA_SUMMARY, text=text)
        raw = ""
        try:
            raw = await self._llm.generate(
                prompt,
                max_tokens=256,
                temperature=0.1,
                stop=["User:", "Available tools:"],
            )
            # Try direct parse first; fall back to extracting the outermost {...}
            stripped = raw.strip()
            try:
                parsed: dict[str, Any] = json.loads(stripped)
            except json.JSONDecodeError:
                # Greedy match to capture outermost JSON object (handles nested braces)
                match = re.search(r"\{.*\}", stripped, re.DOTALL)
                if not match:
                    raise ValueError(f"No JSON found in LLM output: {raw!r}") from None
                parsed = json.loads(match.group())
        except Exception as e:
            logger.warning(
                "CREATIVE LLM parse failed (%s): %r — defaulting to summarize_period", e, raw
            )
            parsed = {"tool": "summarize_period", "payload": {"period": "today"}}

        tool_name = str(parsed.get("tool", "summarize_period"))
        payload: dict[str, Any] = parsed.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        if tool_name not in agent_tools.TOOL_SCHEMAS:
            logger.warning("CREATIVE returned unknown tool %r, falling back to ask_user", tool_name)
            tool_name = "ask_user"
            payload = {"question": text}

        tool_call = ToolCall(tool=tool_name, topic=None, payload=payload)
        decision = self._policy.evaluate(tool_call, text, identity)
        await self._execute_decision(decision, tool_call, text, identity)

    async def _handle_unknown(self, text: str, identity: str) -> None:
        tool_call = ToolCall(tool="ask_user", topic=None, payload={"question": text})
        decision = self._policy.evaluate(tool_call, text, identity)
        await self._execute_decision(decision, tool_call, text, identity)

    async def _execute_decision(
        self,
        decision: Decision,
        tool_call: ToolCall,
        intent_text: str,
        identity: str,
    ) -> None:
        """Execute tool or send confirmation push based on decision."""
        t0 = time.monotonic()

        if decision.action_class == ActionClass.DENY:
            logger.warning("DENY: %s — %s", tool_call.tool, decision.reason)
            await self._pub(
                "agent:result",
                {
                    "type": "result",
                    "action_class": "DENY",
                    "tool": tool_call.tool,
                    "text": decision.reason or "Заблоковано політикою",
                },
            )

        elif decision.action_class == ActionClass.AUTO:
            await self._pub(
                "agent:tool_call",
                {
                    "type": "tool_call",
                    "action_class": "AUTO",
                    "tool": tool_call.tool,
                    "topic": tool_call.topic,
                    "payload": tool_call.payload,
                },
            )
            try:
                result = await self._run_tool(tool_call)
                if isinstance(result, dict | list):
                    result_text = json.dumps(result, ensure_ascii=False, default=str)
                elif result is not None:
                    result_text = str(result)
                else:
                    result_text = "OK"
                await self._pub(
                    "agent:result",
                    {
                        "type": "result",
                        "action_class": "AUTO",
                        "tool": tool_call.tool,
                        "text": result_text[:300],
                    },
                )
            except Exception as exc:
                await self._pub(
                    "agent:result",
                    {
                        "type": "result",
                        "action_class": "ERROR",
                        "tool": tool_call.tool,
                        "text": str(exc),
                    },
                )
                raise

        elif decision.action_class == ActionClass.CONFIRM:
            await self._pub(
                "agent:tool_call",
                {
                    "type": "tool_call",
                    "action_class": "CONFIRM",
                    "tool": tool_call.tool,
                    "text": decision.confirm_message or f"Потрібне підтвердження: {tool_call.tool}",
                    "payload": tool_call.payload,
                },
            )
            await self._handle_confirm(decision, tool_call, intent_text, identity)

        latency_ms = int((time.monotonic() - t0) * 1000)
        await write_audit(decision, tool_call, intent_text, identity, latency_ms)

    async def _handle_confirm(
        self,
        decision: Decision,
        tool_call: ToolCall,
        intent_text: str,
        identity: str,
    ) -> None:
        """Persist ConfirmRequest, push ntfy, wait for Redis confirm:result."""
        import asyncio

        timeout_sec = decision.confirm_timeout_sec or _CONFIRM_DEFAULT_TIMEOUT_SEC
        confirm_id = uuid.uuid4()
        msg = decision.confirm_message or f"Підтвердити: {tool_call.tool}?"

        # Persist ConfirmRequest to DB so the UI can render it
        async with self._get_session() as session:
            if session is not None:
                try:
                    from hub.backend.models import ConfirmRequest  # noqa: PLC0415

                    req = ConfirmRequest(
                        id=confirm_id,
                        expires_at=datetime.now(UTC) + timedelta(seconds=timeout_sec),
                        tool=tool_call.tool,
                        payload=tool_call.payload,
                        intent_text=intent_text,
                        confirm_message=msg,
                    )
                    session.add(req)
                    await session.commit()
                except Exception:
                    logger.exception("Failed to persist ConfirmRequest %s", confirm_id)

        # Notify UI via Redis so ws_confirm WebSocket picks it up
        try:
            await self._redis.publish(
                "confirm:request",
                json.dumps({"id": str(confirm_id), "tool": tool_call.tool, "message": msg}),
            )
        except Exception:
            logger.warning("Failed to publish confirm:request for %s", confirm_id)

        # Send ntfy push so user gets notified outside the web UI too
        try:
            await agent_tools.send_push(
                ntfy_url=f"{settings.ntfy_url}/agent-confirm",
                title="Підтвердження дії",
                message=f"{msg}\n\nID: {confirm_id}",
                priority="high",
            )
        except Exception:
            logger.warning("Failed to send ntfy confirm push for %s", confirm_id)

        # Subscribe and wait for user decision on confirm:result
        pubsub = self._redis.pubsub()
        await pubsub.subscribe("confirm:result")
        approved = False
        try:
            deadline = asyncio.get_event_loop().time() + timeout_sec
            while asyncio.get_event_loop().time() < deadline:
                remaining = deadline - asyncio.get_event_loop().time()
                try:
                    raw = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True),
                        timeout=min(5.0, remaining),
                    )
                except TimeoutError:
                    continue
                if raw is None or raw["type"] != "message":
                    continue
                try:
                    data: dict[str, Any] = json.loads(raw["data"])
                except Exception:
                    continue
                if data.get("id") == str(confirm_id):
                    if data.get("state") == "approved":
                        approved = True
                    break
        finally:
            await pubsub.unsubscribe("confirm:result")
            await pubsub.aclose()

        if approved:
            logger.info("CONFIRM approved for %s — executing tool", confirm_id)
            try:
                result = await self._run_tool(tool_call)
                if isinstance(result, dict | list):
                    result_text = json.dumps(result, ensure_ascii=False, default=str)
                elif result is not None:
                    result_text = str(result)
                else:
                    result_text = "OK"
                await self._pub(
                    "agent:result",
                    {
                        "type": "result",
                        "action_class": "CONFIRM",
                        "tool": tool_call.tool,
                        "text": result_text[:300],
                    },
                )
            except Exception as exc:
                await self._pub(
                    "agent:result",
                    {
                        "type": "result",
                        "action_class": "ERROR",
                        "tool": tool_call.tool,
                        "text": str(exc),
                    },
                )
        else:
            logger.info("CONFIRM timed-out or rejected for %s — not executing", confirm_id)
            await self._pub(
                "agent:result",
                {
                    "type": "result",
                    "action_class": "DENY",
                    "tool": tool_call.tool,
                    "text": "Підтвердження відхилено або вичерпано час",
                },
            )

        # Update DB record state to reflect outcome
        async with self._get_session() as session:
            if session is not None:
                try:
                    from hub.backend.models import ConfirmRequest  # noqa: PLC0415

                    req = await session.get(ConfirmRequest, confirm_id)
                    if req is not None and req.state == "pending":
                        req.state = "timeout" if not approved else "executed"
                        await session.commit()
                except Exception:
                    logger.exception("Failed to update ConfirmRequest %s state", confirm_id)

    async def _run_tool(self, tool_call: ToolCall) -> Any:
        """Dispatch tool_call to the correct tool function."""
        t = tool_call.tool
        p = tool_call.payload

        if t == "mqtt_publish" and tool_call.topic:
            return await agent_tools.mqtt_publish(self._mqtt, tool_call.topic, p)

        elif t == "set_timer":
            return await agent_tools.set_timer(
                self._redis,
                int(p.get("duration_sec", 60)),
                str(p.get("label", "timer")),
            )

        elif t == "send_push":
            return await agent_tools.send_push(
                ntfy_url=f"{settings.ntfy_url}/alerts",
                title=str(p.get("title", "Hub")),
                message=str(p.get("message", "")),
                priority=str(p.get("priority", "default")),
            )

        elif t == "ask_user":
            return await agent_tools.ask_user(self._redis, str(p.get("question", "")))

        elif t == "get_home_state":
            return await agent_tools.get_home_state(self._redis, p.get("room"))

        elif t in ("query_events_db", "summarize_period"):
            async with self._get_session() as session:
                if session is None:
                    logger.warning("No DB session available for tool %s", t)
                    return {"error": "database unavailable"}
                if t == "summarize_period":
                    return await agent_tools.summarize_period(
                        session, str(p.get("period", "today"))
                    )
                return await agent_tools.query_events_db(
                    session,
                    query=str(p.get("query", "")),
                    limit=int(p.get("limit", 10)),
                    since_hours=float(p.get("since_hours", 24.0)),
                )

        else:
            logger.warning("Unknown tool: %s", t)
            return None

    async def run(self) -> None:
        """Subscribe to voice/command and process commands."""
        logger.info("Orchestrator starting, subscribing to voice/command")
        async with self._mqtt:
            await self._mqtt.subscribe("voice/command")
            async for message in self._mqtt.messages:
                try:
                    data = json.loads(message.payload)
                    text = str(data.get("text", ""))
                    if text:
                        await self.handle_command(text)
                except Exception:
                    logger.exception("Failed to process message: %s", message.payload)
