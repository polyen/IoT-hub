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
from prometheus_client import Counter
from sqlalchemy.ext.asyncio import AsyncSession

from hub.backend.config import settings
from hub.edge.agent import tools as agent_tools
from hub.edge.agent.grammars import load_grammar
from hub.edge.agent.llm_local import LocalLLMClient
from hub.edge.agent.policy import (
    ActionClass,
    Decision,
    PolicyEngine,
    ToolCall,
    write_audit,
)
from hub.edge.agent.router import IntentClass, IntentRouter

# Routing-branch telemetry — drives the LLM-optimization decision.  Label values:
#   deterministic_resolved | deterministic_failed | structured | creative |
#   unknown | reasoner_success | reasoner_failure
AGENT_ROUTING = Counter(
    "iot_hub_agent_routing_branch_total",
    "Voice/text commands grouped by which orchestrator branch handled them",
    ["branch"],
)

# How long to wait for user confirmation before auto-denying
_CONFIRM_DEFAULT_TIMEOUT_SEC = 60

# After this many seconds the cached ArcFace identity is considered stale and
# is no longer used as the "speaker room" fallback in TextResolver.  A guest
# spotted by the camera 10 minutes ago is not the current speaker.
_IDENTITY_TTL_SEC = 60.0

logger = logging.getLogger(__name__)

# System prompt for CREATIVE branch — tells the LLM which tools it can call
_CREATIVE_SYSTEM = """You are a smart home assistant. Choose ONE tool and respond with valid JSON only.

Available tools:
{tool_schemas}

Respond ONLY with a JSON object like: {{"tool": "<name>", "payload": {{...}}}}
Pick the most relevant tool. Use summarize_period for summary/report requests, query_events_db for specific event lookups, get_home_state for current state, send_push for alerts, ask_user if unclear."""

_TOOL_SCHEMA_SUMMARY = "\n".join(
    f"- {name}: {list(schema.get('properties', {}).keys())}"
    for name, schema in agent_tools.TOOL_SCHEMAS.items()
)

_PERIOD_UA = {"today": "сьогодні", "yesterday": "вчора", "week": "цього тижня"}
_EVENT_UA: dict[str, str] = {
    "camera/event": "з камер",
    "event/fused": "сенсорних",
    "motion": "руху",
    "fire": "пожежі",
    "smoke": "диму",
    "fall": "падінь",
}


def _result_to_speech(tool_call: ToolCall, result: Any) -> str:
    """Format a tool result as a short spoken Ukrainian phrase for TTS."""
    tool = tool_call.tool

    if tool == "summarize_period":
        if not isinstance(result, dict):
            return "Звіт готовий."
        period = _PERIOD_UA.get(result.get("period", "today"), result.get("period", ""))
        counts: dict[str, int] = result.get("event_counts", {})
        if not counts:
            return f"За {period} подій не зафіксовано."
        total = sum(counts.values())
        top = sorted(counts.items(), key=lambda x: -x[1])[:2]
        parts = [f"{cnt} {_EVENT_UA.get(t, t.split('/')[-1])}" for t, cnt in top]
        return f"За {period} {total} подій: {', '.join(parts)}."

    if tool == "query_events_db":
        if not isinstance(result, list) or not result:
            return "Подій не знайдено."
        n = len(result)
        ev = result[0]
        room = ev.get("room") or ""
        etype = _EVENT_UA.get(ev.get("type", ""), ev.get("type", "").split("/")[-1])
        room_part = f" у {room}" if room else ""
        return f"Знайдено {n} подій. Остання: {etype}{room_part}."

    if tool == "get_home_state":
        if not isinstance(result, dict) or not result:
            return "Дані про стан будинку відсутні."
        parts = []
        for room, data in list(result.items())[:2]:
            if not isinstance(data, dict):
                continue
            sensors = []
            if "temperature" in data:
                sensors.append(f"{data['temperature']}°")
            if "humidity" in data:
                sensors.append(f"вологість {data['humidity']}%")
            if sensors:
                parts.append(f"{room}: {', '.join(sensors)}")
        return ("Стан будинку: " + "; ".join(parts) + ".") if parts else "Дані оновлено."

    if tool == "set_timer":
        if isinstance(result, dict):
            sec = int(result.get("duration_sec", 0))
            label = result.get("label", "")
            if sec >= 3600:
                t = f"{sec // 3600} год"
            elif sec >= 60:
                t = f"{sec // 60} хв"
            else:
                t = f"{sec} с"
            suffix = f" «{label}»" if label and label != "timer" else ""
            return f"Таймер{suffix} на {t}."
        return "Таймер встановлено."

    if tool == "send_push":
        return "Сповіщення надіслано."

    if tool == "ask_user":
        if isinstance(result, dict):
            return str(result.get("question", "Уточніть команду."))
        return "Уточніть команду."

    if tool == "mqtt_publish":
        topic = tool_call.topic or ""
        payload = tool_call.payload or {}
        state = payload.get("state", "")
        if "light" in topic:
            if state == "on":
                return "Світло увімкнено."
            if state == "off":
                return "Світло вимкнено."
            return "Світло перемкнено."
        if "relay" in topic or "switch" in topic:
            return "Пристрій перемкнено."
        return "Виконано."

    return "Готово."


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
        device_registry: Any | None = None,
        state_verifier: Any | None = None,
        llm_reasoner: Any | None = None,
    ) -> None:
        self._policy = policy
        self._router = router
        self._llm = llm
        self._redis = redis_client
        self._mqtt = mqtt_client
        self._session_factory = session_factory
        self._max_tool_calls = max_tool_calls_per_turn
        self._state_verifier = state_verifier
        self._llm_reasoner = llm_reasoner

        # ArcFace identity state (updated by identity MQTT subscription in run())
        self._last_identity: str = "default"
        self._last_identity_room: str | None = None
        self._last_identity_ts: float = 0.0

        # Recent command history for LLM context (last 10 turns)
        self._command_history: list[Any] = []

        # TextResolver (rules-based UA command parser)
        if device_registry is not None:
            from hub.edge.agent.text_resolver import TextResolver  # noqa: PLC0415

            self._text_resolver: Any | None = TextResolver(device_registry)
        else:
            self._text_resolver = None

        # SceneEngine — loaded only when device registry is available
        self._scene_engine: Any | None = None
        if device_registry is not None:
            try:
                from hub.edge.agent.scene_engine import SceneEngine  # noqa: PLC0415

                engine = SceneEngine()
                engine.load()
                if engine.is_loaded:
                    self._scene_engine = engine
            except Exception as exc:
                logger.warning("SceneEngine unavailable: %s", exc)

        # Keep registry reference for SceneEngine.plan() calls
        self._device_registry = device_registry

    def _fresh_speaker_room(self) -> str | None:
        """Return last ArcFace room if seen within ``_IDENTITY_TTL_SEC``, else None.

        Without this, a guest spotted by the camera 10 min ago would still be
        treated as the current speaker by TextResolver, causing the resolver to
        fall back to the wrong room when the command omits one.
        """
        if self._last_identity_ts == 0.0:
            return None
        if (time.monotonic() - self._last_identity_ts) > _IDENTITY_TTL_SEC:
            return None
        return self._last_identity_room

    @asynccontextmanager
    async def _get_session(self) -> AsyncGenerator[AsyncSession | None, None]:
        if self._session_factory is None:
            yield None
            return
        async with self._session_factory() as session:
            yield session

    async def _pub(self, channel: str, payload: dict[str, Any]) -> None:
        """Publish a progress event to Redis pub/sub; persist turn events to agent:history."""
        try:
            payload.setdefault("ts", datetime.now(UTC).isoformat())
            serialized = json.dumps(payload)
            await self._redis.publish(channel, serialized)
            if channel in ("agent:turn", "agent:tool_call", "agent:result"):
                await self._redis.lpush("agent:history", serialized)
                await self._redis.ltrim("agent:history", 0, 99)
        except Exception:
            pass

    async def handle_command(
        self, text: str, identity: str = "default", forced_device_id: str | None = None
    ) -> None:
        """Process one voice/text command end-to-end."""
        t0 = time.monotonic()

        # Track command history for LLM context
        from hub.edge.agent.llm_reasoning import Turn  # noqa: PLC0415

        self._command_history.append(Turn(text=text))
        if len(self._command_history) > 10:
            self._command_history = self._command_history[-10:]

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
                # Sub-branch (resolved/failed) counted inside _handle_deterministic
                await self._handle_deterministic(text, identity, intent, forced_device_id)
            elif intent.class_ == IntentClass.STRUCTURED:
                AGENT_ROUTING.labels(branch="structured").inc()
                await self._handle_structured(text, identity, intent)
            elif intent.class_ == IntentClass.CREATIVE:
                # When the ML classifier set a known query/scene prototype, route
                # directly without LLM — count as intent_classifier branch.
                if intent.prototype in (
                    "query_temperature",
                    "query_humidity",
                    "query_state",
                    "summarize_events",
                    "scene_generic",
                ):
                    AGENT_ROUTING.labels(branch="intent_classifier").inc()
                else:
                    AGENT_ROUTING.labels(branch="creative").inc()
                await self._handle_creative(text, identity, intent)
            else:
                AGENT_ROUTING.labels(branch="unknown").inc()
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

    async def _handle_deterministic(
        self, text: str, identity: str, intent: Any, forced_device_id: str | None = None
    ) -> None:
        """Resolve device command via TextResolver, then route through PolicyEngine."""
        if self._text_resolver is None:
            # No registry available — cannot resolve; fall back to ask_user
            AGENT_ROUTING.labels(branch="deterministic_no_registry").inc()
            tool_call = ToolCall(tool="ask_user", topic=None, payload={"question": text})
            decision = self._policy.evaluate(tool_call, text, identity)
            await self._execute_decision(decision, tool_call, text, identity)
            return

        speaker_room: str | None = self._fresh_speaker_room()
        resolution = await self._text_resolver.resolve(
            text, intent.prototype, speaker_room, forced_device_id
        )

        if not resolution.success:
            AGENT_ROUTING.labels(branch="deterministic_failed").inc()
            # Before emitting failure, try LLM reasoner as fallback (when not forced)
            if forced_device_id is None and self._llm_reasoner is not None:
                await self._handle_with_reasoner(text, identity)
                return
            await self._emit_explainable_failure(resolution, text, identity)
            return

        AGENT_ROUTING.labels(branch="deterministic_resolved").inc()

        # Broadcast: publish to every matched device
        targets = (
            resolution.all_devices
            if resolution.all_devices
            else ([resolution.device] if resolution.device else [])
        )
        for device in targets:
            payload = self._build_mqtt_payload(resolution, device)
            tool_call = ToolCall(
                tool="mqtt_publish",
                topic=device.mqtt_command_topic,
                payload=payload,
            )
            decision = self._policy.evaluate(tool_call, text, identity)
            await self._execute_decision(decision, tool_call, text, identity)

            # Phase 3: verify device acknowledged the command via its state topic
            if (
                decision.action_class == ActionClass.AUTO
                and device.mqtt_state_topic
                and self._state_verifier is not None
            ):
                await self._verify_state_change(device, payload)

    async def _verify_state_change(self, device: Any, payload: dict[str, Any]) -> None:
        """Call StateVerifier and override agent:result with WARN if device didn't respond."""
        from hub.edge.agent.state_verifier import VerificationResult  # noqa: PLC0415

        if self._state_verifier is None:
            return
        result = await self._state_verifier.expect_change(
            device_id=device.device_id,
            expected=payload,
        )
        if result == VerificationResult.TIMEOUT:
            speech = "Команду відправив, але пристрій не відповів. Можливо, він офлайн."
            await self._pub(
                "agent:result",
                {
                    "type": "result",
                    "action_class": "WARN",
                    "tool": "mqtt_publish",
                    "text": speech,
                },
            )
        elif result == VerificationResult.MISMATCH:
            speech = "Пристрій не змінив стан. Можливо, заблоковано локально."
            await self._pub(
                "agent:result",
                {
                    "type": "result",
                    "action_class": "WARN",
                    "tool": "mqtt_publish",
                    "text": speech,
                },
            )
        # CONFIRMED and STATE_NOT_TRACKED: original speech stands

    async def _emit_explainable_failure(self, resolution: Any, text: str, identity: str) -> None:
        """Publish an INFO result with a UA-rendered explanation of why resolution failed.

        For AMBIGUOUS failures, also publishes a ``confirm:request`` event so the
        UI can render a device-picker instead of requiring spoken disambiguation.

        Always writes an AgentAudit row with action_class=INFO, executed=False so
        failures are observable in the audit log and evaluation suite.
        """
        from hub.edge.agent.i18n_uk import render_failure  # noqa: PLC0415
        from hub.edge.agent.text_resolver import ResolutionFailureKind  # noqa: PLC0415

        msg = render_failure(resolution)
        logger.info(
            "Explainable failure [%s]: %r — %s",
            resolution.failure_kind,
            text,
            resolution.reasoning,
        )

        result_payload: dict[str, Any] = {
            "type": "result",
            "action_class": "INFO",
            "text": msg,
            "failure_kind": (resolution.failure_kind.value if resolution.failure_kind else None),
            "reasoning": resolution.reasoning,
        }

        # Include candidates in agent:result so the Stack tab can render a picker inline
        if resolution.failure_kind == ResolutionFailureKind.AMBIGUOUS and resolution.candidates:
            result_payload["candidates"] = [
                {
                    "device_id": d.device_id,
                    "label": d.label,
                    "room": d.room_name_ua,
                    "kind": d.kind,
                }
                for d in resolution.candidates
            ]

        await self._pub("agent:result", result_payload)

        # For AMBIGUOUS, also push a structured candidate list to confirm:request channel
        if resolution.failure_kind == ResolutionFailureKind.AMBIGUOUS and resolution.candidates:
            candidates = [
                {
                    "device_id": d.device_id,
                    "label": d.label,
                    "room": d.room_name_ua,
                    "kind": d.kind,
                }
                for d in resolution.candidates
            ]
            try:
                await self._redis.publish(
                    "confirm:request",
                    json.dumps(
                        {
                            "type": "ambiguity",
                            "question": msg,
                            "candidates": candidates,
                            "original_text": text,
                        }
                    ),
                )
            except Exception:
                logger.warning("Failed to publish ambiguity confirm:request")

        # Persist an audit row so failures are visible in /api/agent/audit + eval reports
        try:
            await write_audit(
                Decision(action_class=ActionClass.INFO, reason=msg),
                ToolCall(tool="(none)", topic=None, payload={}),
                text,
                identity,
                latency_ms=0,
                executed=False,
            )
        except Exception:
            logger.exception("Failed to write audit row for explainable failure")

    @staticmethod
    def _build_mqtt_payload(resolution: Any, device: Any) -> dict[str, Any]:
        """Build MQTT command payload for a single device from a successful Resolution."""
        action = resolution.action or "on"
        if action in ("on", "open"):
            base: dict[str, Any] = dict(device.payload_on) if device.payload_on else {"state": "ON"}
        elif action in ("off", "close"):
            base = dict(device.payload_off) if device.payload_off else {"state": "OFF"}
        elif action == "toggle":
            base = {"state": "TOGGLE"}
        else:
            # set / brightness_set / temp_set / inc / dec — use params directly
            base = {}
        base.update(resolution.params)
        return base

    async def _handle_structured(self, text: str, identity: str, intent: Any = None) -> None:
        """Use constrained LLM generation to produce tool call JSON.

        When LLMReasoner is available it provides chain-of-thought reasoning before
        the constrained generation which improves accuracy.
        """
        if self._llm_reasoner is not None:
            await self._handle_with_reasoner(text, identity)
            return

        prototype = getattr(intent, "prototype", None) if intent else None
        lower = text.lower()
        # Grammar selection: handle both legacy prototype names and new ML label names
        if prototype in ("temp_set", "thermostat_set") or any(
            kw in lower for kw in ("температур", "градус", "°")
        ):
            grammar_name = "thermostat"
        elif prototype in (
            "brightness_set",
            "light_brightness_set",
            "light_color_set",
        ) or any(kw in lower for kw in ("яскравість", "відсоток")):
            grammar_name = "light"
        elif any(kw in lower for kw in ("таймер", "нагадай", "timer", "remind")):
            grammar_name = "timer"
        elif any(kw in lower for kw in ("relay", "реле", "розетк")):
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

    async def _handle_creative(self, text: str, identity: str, intent: Any = None) -> None:
        """Route query/scene intents directly to tools (no LLM).

        Falls back to LLMReasoner when available, then to LLM chat for any
        prototype not covered by direct routing.
        """
        prototype = getattr(intent, "prototype", None) if intent else None

        # Direct routing for ML-classified query/scene intents (no LLM needed)
        if prototype in ("query_temperature", "query_humidity", "query_state"):
            room: str | None = None  # TextResolver room hint not available here; let tool scan all
            tool_call = ToolCall(tool="get_home_state", topic=None, payload={"room": room})
            decision = self._policy.evaluate(tool_call, text, identity)
            await self._execute_decision(decision, tool_call, text, identity)
            return

        if prototype == "summarize_events":
            tool_call = ToolCall(tool="summarize_period", topic=None, payload={"period": "today"})
            decision = self._policy.evaluate(tool_call, text, identity)
            await self._execute_decision(decision, tool_call, text, identity)
            return

        if prototype == "scene_generic":
            if self._scene_engine is not None and self._device_registry is not None:
                scene_name = self._scene_engine.match(text)
                if scene_name:
                    AGENT_ROUTING.labels(branch="scene_engine").inc()
                    tool_calls = await self._scene_engine.plan(
                        scene_name,
                        registry=self._device_registry,
                        speaker_room=self._fresh_speaker_room(),
                    )
                    if tool_calls:
                        desc = self._scene_engine.description(scene_name)
                        await self._pub(
                            "agent:turn",
                            {"type": "scene", "name": scene_name, "description": desc},
                        )
                        for tc in tool_calls:
                            decision = self._policy.evaluate(tc, text, identity)
                            await self._execute_decision(decision, tc, text, identity)
                        return
                    # Scene found but no devices matched
                    logger.info(
                        "SceneEngine: scene %r matched but no devices available", scene_name
                    )

            # No scene matched or no registry — ask which scene to activate
            available = (
                ", ".join(self._scene_engine.scene_names)
                if self._scene_engine and self._scene_engine.is_loaded
                else "кіно, ніч, ранок"
            )
            tool_call = ToolCall(
                tool="ask_user",
                topic=None,
                payload={"question": f"Яку сцену активувати? Доступні: {available}"},
            )
            decision = self._policy.evaluate(tool_call, text, identity)
            await self._execute_decision(decision, tool_call, text, identity)
            return

        # LLM-based reasoning (optional; disabled by default after Phase 2)
        if self._llm_reasoner is not None:
            await self._handle_with_reasoner(text, identity)
            return

        system = _CREATIVE_SYSTEM.format(tool_schemas=_TOOL_SCHEMA_SUMMARY)
        raw = ""
        parsed: dict[str, Any] = {}
        try:
            raw = await self._llm.generate_chat(
                system=system,
                user=text,
                max_tokens=64,
                temperature=0.1,
            )
            stripped = raw.strip()
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                match = re.search(r"\{.*\}", stripped, re.DOTALL)
                if not match:
                    raise ValueError(f"No JSON in LLM output: {raw!r}") from None
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

    async def _handle_with_reasoner(self, text: str, identity: str) -> None:
        """Run 2-turn LLM pipeline (reasoning → constrained tool call).

        Publishes a ``reasoning`` event to ``agent:turn`` for the UI reasoning fold,
        then executes the resulting tool call through the normal PolicyEngine path.
        """
        from hub.edge.agent.llm_reasoning import Turn  # noqa: PLC0415

        history = [Turn(text=t.text) for t in self._command_history[:-1]]  # exclude current turn

        result = await self._llm_reasoner.reason_and_act(text, history)  # type: ignore[union-attr]

        # Always publish reasoning so the UI reasoning fold has content
        await self._pub(
            "agent:turn",
            {
                "type": "reasoning",
                "text": result.reasoning or "(немає роздумів)",
                "source": "llm_reasoner",
            },
        )

        if not result.success:
            AGENT_ROUTING.labels(branch="reasoner_failure").inc()
            await self._pub(
                "agent:result",
                {
                    "type": "result",
                    "action_class": "INFO",
                    "text": result.failure_reason or "Не вдалося виконати команду через LLM.",
                    "failure_kind": "UNCLEAR_INTENT",
                },
            )
            return

        AGENT_ROUTING.labels(branch="reasoner_success").inc()

        # Look up the actual MQTT command topic from the registry instead of
        # synthesizing one — the registry is the only place that knows the real
        # topic (it may be overridden in DevicePlacement.config["mqtt_topic"]).
        device = None
        if self._text_resolver is not None:
            all_devices = await self._text_resolver._registry.all()
            device = next((d for d in all_devices if d.device_id == result.device_id), None)
        if device is None:
            await self._pub(
                "agent:result",
                {
                    "type": "result",
                    "action_class": "INFO",
                    "text": f"Пристрій «{result.device_id}» не знайдено у реєстрі.",
                    "failure_kind": "device_not_found",
                    "reasoning": result.reasoning,
                },
            )
            return

        payload: dict[str, Any] = {}
        action = result.action or "on"
        if action == "on":
            payload = dict(device.payload_on) if device.payload_on else {"state": "ON"}
        elif action == "off":
            payload = dict(device.payload_off) if device.payload_off else {"state": "OFF"}
        elif action == "toggle":
            payload = {"state": "TOGGLE"}
        elif action == "brightness_set":
            payload = {"brightness": result.params.get("brightness", 128)}
        elif action == "temp_set":
            payload = {"temperature": result.params.get("temperature", 20)}
        else:
            payload = result.params or {"state": "ON"}

        tool_call = ToolCall(
            tool="mqtt_publish",
            topic=device.mqtt_command_topic,
            payload=payload,
        )
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
        """Execute tool or send confirmation push based on decision.

        Tracks ``executed`` so the AgentAudit row reflects whether the tool
        actually ran (AUTO success or CONFIRM-approved success) instead of
        always being False.
        """
        t0 = time.monotonic()
        executed = False

        if decision.action_class == ActionClass.DENY:
            logger.warning("DENY: %s — %s", tool_call.tool, decision.reason)
            from hub.edge.agent.i18n_uk import render_deny  # noqa: PLC0415

            await self._pub(
                "agent:result",
                {
                    "type": "result",
                    "action_class": "DENY",
                    "tool": tool_call.tool,
                    "text": render_deny(decision.reason),
                    "reasoning": decision.reason,
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
                executed = True
                raw = (
                    json.dumps(result, ensure_ascii=False, default=str)
                    if isinstance(result, dict | list)
                    else str(result or "OK")
                )
                await self._pub(
                    "agent:result",
                    {
                        "type": "result",
                        "action_class": "AUTO",
                        "tool": tool_call.tool,
                        "text": _result_to_speech(tool_call, result),
                        "data": raw[:500],
                    },
                )
            except Exception as exc:
                executed = False
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
            executed = await self._handle_confirm(decision, tool_call, intent_text, identity)

        latency_ms = int((time.monotonic() - t0) * 1000)
        await write_audit(decision, tool_call, intent_text, identity, latency_ms, executed=executed)

    async def _handle_confirm(
        self,
        decision: Decision,
        tool_call: ToolCall,
        intent_text: str,
        identity: str,
    ) -> bool:
        """Persist ConfirmRequest, push ntfy, wait for Redis confirm:result.

        Returns True if the user approved AND the tool ran without error;
        False on rejection, timeout, or tool exception.
        """
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

        executed = False
        if approved:
            logger.info("CONFIRM approved for %s — executing tool", confirm_id)
            try:
                result = await self._run_tool(tool_call)
                executed = True
                raw = (
                    json.dumps(result, ensure_ascii=False, default=str)
                    if isinstance(result, dict | list)
                    else str(result or "OK")
                )
                await self._pub(
                    "agent:result",
                    {
                        "type": "result",
                        "action_class": "CONFIRM",
                        "tool": tool_call.tool,
                        "text": _result_to_speech(tool_call, result),
                        "data": raw[:500],
                    },
                )
            except Exception as exc:
                executed = False
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

        return executed

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
        """Subscribe to voice/command and home/+/camera/identity; process commands."""
        logger.info("Orchestrator starting, subscribing to voice/command and camera identity")
        async with self._mqtt:
            await self._mqtt.subscribe("voice/command")
            await self._mqtt.subscribe("home/+/camera/identity")
            async for message in self._mqtt.messages:
                topic = str(message.topic)
                try:
                    data = json.loads(message.payload)
                    if topic.endswith("/camera/identity"):
                        # Update speaker-room context from ArcFace face recognition
                        self._last_identity = str(data.get("identity", "default"))
                        self._last_identity_room = data.get("room") or None
                        self._last_identity_ts = time.monotonic()
                        logger.debug(
                            "Identity update: %r in room %r",
                            self._last_identity,
                            self._last_identity_room,
                        )
                        continue
                    text = str(data.get("text", ""))
                    if text:
                        await self._redis.publish(
                            "voice:transcript",
                            json.dumps(
                                {
                                    "type": "transcript",
                                    "text": text,
                                    "ts": datetime.now(UTC).isoformat(),
                                }
                            ),
                        )
                        forced_device_id = data.get("forced_device_id") or None
                        await self.handle_command(text, self._last_identity, forced_device_id)
                except Exception:
                    logger.exception("Failed to process message: %s", message.payload)
