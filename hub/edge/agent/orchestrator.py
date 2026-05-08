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
import time
from typing import Any

import aiomqtt
import redis.asyncio as aioredis

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

logger = logging.getLogger(__name__)

# Deterministic routing table: intent prototype label → tool name
DETERMINISTIC_MAP: dict[str, str] = {
    "light_on": "mqtt_publish",
    "light_off": "mqtt_publish",
    "device_off": "mqtt_publish",
    "relay_toggle": "mqtt_publish",
    "timer_set": "set_timer",
}


class AgentOrchestrator:
    def __init__(
        self,
        policy: PolicyEngine,
        router: IntentRouter,
        llm: LocalLLMClient,
        redis_client: aioredis.Redis,
        mqtt_client: aiomqtt.Client,
        max_tool_calls_per_turn: int = 5,
    ) -> None:
        self._policy = policy
        self._router = router
        self._llm = llm
        self._redis = redis_client
        self._mqtt = mqtt_client
        self._max_tool_calls = max_tool_calls_per_turn

    async def handle_command(self, text: str, identity: str = "default") -> None:
        """Process one voice/text command end-to-end."""
        t0 = time.monotonic()
        intent = self._router.classify_intent(text)
        logger.info("Intent: %s (score=%.2f) — %r", intent.class_, intent.score, text)

        try:
            if intent.class_ == IntentClass.DETERMINISTIC:
                await self._handle_deterministic(text, identity, intent)
            elif intent.class_ == IntentClass.STRUCTURED:
                await self._handle_structured(text, identity)
            elif intent.class_ == IntentClass.CREATIVE:
                await self._handle_creative(text, identity)
            else:
                await self._handle_unknown(text, identity)
        except Exception:
            logger.exception("Orchestrator error handling command: %r", text)
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

    async def _handle_structured(self, text: str, identity: str) -> None:
        """Use constrained LLM generation to produce tool call JSON."""
        grammar = load_grammar("light")  # simplified: always use light grammar for now
        prompt = f"Convert to JSON tool call: {text}\nJSON:"
        try:
            result = await self._llm.generate_constrained(prompt, grammar)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Constrained generation failed: %s — falling back to ask_user", e)
            result = {"tool": "ask_user", "question": "Sorry, I didn't understand that."}

        tool_name = str(result.get("tool", "ask_user"))
        topic = result.get("topic")
        payload = result.get("payload", result)
        tool_call = ToolCall(tool=tool_name, topic=topic, payload=payload)
        decision = self._policy.evaluate(tool_call, text, identity)
        await self._execute_decision(decision, tool_call, text, identity)

    async def _handle_creative(self, text: str, identity: str) -> None:
        """Use unconstrained LLM for summaries, questions."""
        tool_call = ToolCall(tool="summarize_period", topic=None, payload={"period": "today"})
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

        elif decision.action_class == ActionClass.AUTO:
            await self._run_tool(tool_call)

        elif decision.action_class == ActionClass.CONFIRM:
            msg = decision.confirm_message or f"Confirm: {tool_call.tool}?"
            await agent_tools.send_push(
                ntfy_url=f"{settings.ntfy_url}/agent-confirm",
                title="Підтвердження",
                message=msg,
                priority="high",
            )
            logger.info("CONFIRM sent, waiting for response (not yet implemented)")

        latency_ms = int((time.monotonic() - t0) * 1000)
        await write_audit(decision, tool_call, intent_text, identity, latency_ms)

    async def _run_tool(self, tool_call: ToolCall) -> Any:
        """Dispatch tool_call to the correct tool function."""
        if tool_call.tool == "mqtt_publish" and tool_call.topic:
            return await agent_tools.mqtt_publish(self._mqtt, tool_call.topic, tool_call.payload)
        elif tool_call.tool == "set_timer":
            return await agent_tools.set_timer(
                self._redis,
                tool_call.payload.get("duration_sec", 60),
                tool_call.payload.get("label", "timer"),
            )
        elif tool_call.tool == "send_push":
            return await agent_tools.send_push(
                ntfy_url=f"{settings.ntfy_url}/alerts",
                **tool_call.payload,
            )
        elif tool_call.tool == "ask_user":
            return await agent_tools.ask_user(self._redis, tool_call.payload.get("question", ""))
        else:
            logger.warning("Unknown tool: %s", tool_call.tool)
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
