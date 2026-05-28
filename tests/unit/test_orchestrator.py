from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hub.edge.agent.llm_local import LocalLLMClient
from hub.edge.agent.orchestrator import AgentOrchestrator
from hub.edge.agent.policy import ActionClass, Decision, PolicyEngine, ToolCall
from hub.edge.agent.router import Intent, IntentClass, IntentRouter


def make_orchestrator(
    intent_class: IntentClass = IntentClass.UNKNOWN,
    action_class: ActionClass = ActionClass.AUTO,
    prototype: str | None = None,
    llm_result: dict | None = None,
    llm_generate_result: str | None = None,
) -> tuple[AgentOrchestrator, MagicMock, AsyncMock, AsyncMock]:
    router = MagicMock(spec=IntentRouter)
    router.classify_intent.return_value = Intent(
        class_=intent_class, score=0.9, prototype=prototype
    )

    policy = MagicMock(spec=PolicyEngine)
    policy.evaluate.return_value = Decision(action_class=action_class, reason="test_reason")

    llm = AsyncMock(spec=LocalLLMClient)
    if llm_result is not None:
        llm.generate_constrained.return_value = llm_result
    else:
        llm.generate_constrained.return_value = {
            "tool": "ask_user",
            "question": "unclear",
        }
    default_generate = json.dumps({"tool": "summarize_period", "payload": {"period": "today"}})
    response = llm_generate_result if llm_generate_result is not None else default_generate
    # _handle_creative uses generate_chat; some callers also patch generate directly
    llm.generate.return_value = response
    llm.generate_chat.return_value = response

    redis_client = AsyncMock()
    mqtt_client = AsyncMock()

    orch = AgentOrchestrator(
        policy=policy,
        router=router,
        llm=llm,
        redis_client=redis_client,
        mqtt_client=mqtt_client,
    )
    return orch, policy, llm, redis_client


# ── 1: UNKNOWN intent → ask_user tool called ──────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_intent_calls_ask_user() -> None:
    orch, policy, _, redis = make_orchestrator(
        intent_class=IntentClass.UNKNOWN,
        action_class=ActionClass.AUTO,
    )

    with (
        patch("hub.edge.agent.orchestrator.write_audit", new_callable=AsyncMock),
        patch(
            "hub.edge.agent.orchestrator.agent_tools.ask_user", new_callable=AsyncMock
        ) as mock_ask,
    ):
        mock_ask.return_value = {"status": "sent"}
        await orch.handle_command("gibberish text nobody understands")

    policy.evaluate.assert_called_once()
    call_args = policy.evaluate.call_args[0]
    tool_call: ToolCall = call_args[0]
    assert tool_call.tool == "ask_user"


# ── 2: DENY decision → write_audit called, tool NOT executed ──────────────────


@pytest.mark.asyncio
async def test_deny_decision_no_tool_execution() -> None:
    orch, policy, _, redis = make_orchestrator(
        intent_class=IntentClass.UNKNOWN,
        action_class=ActionClass.DENY,
    )

    with (
        patch("hub.edge.agent.orchestrator.write_audit", new_callable=AsyncMock) as mock_audit,
        patch(
            "hub.edge.agent.orchestrator.agent_tools.ask_user", new_callable=AsyncMock
        ) as mock_ask,
        patch(
            "hub.edge.agent.orchestrator.agent_tools.mqtt_publish", new_callable=AsyncMock
        ) as mock_pub,
    ):
        await orch.handle_command("some command")

    mock_audit.assert_called_once()
    audit_decision: Decision = mock_audit.call_args[0][0]
    assert audit_decision.action_class == ActionClass.DENY
    assert mock_audit.call_args.kwargs.get("executed") is False
    mock_ask.assert_not_called()
    mock_pub.assert_not_called()


# ── 3: AUTO decision → tool executed ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_decision_executes_tool() -> None:
    orch, policy, _, redis = make_orchestrator(
        intent_class=IntentClass.UNKNOWN,
        action_class=ActionClass.AUTO,
    )

    with (
        patch("hub.edge.agent.orchestrator.write_audit", new_callable=AsyncMock) as mock_audit,
        patch(
            "hub.edge.agent.orchestrator.agent_tools.ask_user", new_callable=AsyncMock
        ) as mock_ask,
    ):
        mock_ask.return_value = {"status": "sent"}
        await orch.handle_command("some unknown command")

    mock_ask.assert_called_once()
    mock_audit.assert_called_once()
    audit_decision: Decision = mock_audit.call_args[0][0]
    assert audit_decision.action_class == ActionClass.AUTO
    # executed=True now reflects that the tool actually ran
    assert mock_audit.call_args.kwargs.get("executed") is True


# ── 4: STRUCTURED intent → llm.generate_constrained called ───────────────────


@pytest.mark.asyncio
async def test_structured_intent_calls_generate_constrained() -> None:
    llm_return = {"tool": "ask_user", "question": "What brightness?"}
    orch, policy, llm, _ = make_orchestrator(
        intent_class=IntentClass.STRUCTURED,
        action_class=ActionClass.AUTO,
        llm_result=llm_return,
    )

    with (
        patch("hub.edge.agent.orchestrator.write_audit", new_callable=AsyncMock),
        patch(
            "hub.edge.agent.orchestrator.agent_tools.ask_user", new_callable=AsyncMock
        ) as mock_ask,
    ):
        mock_ask.return_value = {"status": "sent"}
        await orch.handle_command("встанови яскравість 50 відсотків")

    llm.generate_constrained.assert_called_once()
    call_args = llm.generate_constrained.call_args
    assert "grammar" in str(call_args) or call_args[0][1] or call_args[1].get("grammar")


# ── 5: Exception in tool handled gracefully (no crash) ────────────────────────


@pytest.mark.asyncio
async def test_orchestrator_handles_tool_exception_gracefully() -> None:
    orch, policy, _, redis = make_orchestrator(
        intent_class=IntentClass.UNKNOWN,
        action_class=ActionClass.AUTO,
    )

    with (
        patch("hub.edge.agent.orchestrator.write_audit", new_callable=AsyncMock),
        patch(
            "hub.edge.agent.orchestrator.agent_tools.ask_user",
            new_callable=AsyncMock,
            side_effect=RuntimeError("tool exploded"),
        ),
    ):
        # Must not raise — exceptions are caught and logged
        await orch.handle_command("some unknown command")


# ── 6: CREATIVE intent → llm.generate called, tool dispatched ────────────────


@pytest.mark.asyncio
async def test_creative_intent_calls_generate() -> None:
    llm_response = json.dumps({"tool": "summarize_period", "payload": {"period": "today"}})
    orch, policy, llm, _ = make_orchestrator(
        intent_class=IntentClass.CREATIVE,
        action_class=ActionClass.AUTO,
        llm_generate_result=llm_response,
    )

    with (
        patch("hub.edge.agent.orchestrator.write_audit", new_callable=AsyncMock),
        patch(
            "hub.edge.agent.orchestrator.agent_tools.summarize_period", new_callable=AsyncMock
        ) as mock_summarize,
    ):
        mock_summarize.return_value = {"summary": "quiet day"}
        # summarize_period needs a DB session — supply session_factory returning None
        orch._session_factory = None
        await orch.handle_command("що цікавого сталось вчора?")

    llm.generate_chat.assert_called_once()
    kwargs = llm.generate_chat.call_args.kwargs
    # System prompt must reference tool schemas and the user message is the command
    assert "tool" in kwargs["system"].lower() or "summarize" in kwargs["system"].lower()
    assert "вчора" in kwargs["user"]


@pytest.mark.asyncio
async def test_creative_intent_unknown_tool_falls_back_to_ask_user() -> None:
    """If LLM returns a tool name not in TOOL_SCHEMAS, fall back to ask_user."""
    llm_response = json.dumps({"tool": "nonexistent_tool_xyz", "payload": {}})
    orch, policy, llm, _ = make_orchestrator(
        intent_class=IntentClass.CREATIVE,
        action_class=ActionClass.AUTO,
        llm_generate_result=llm_response,
    )

    with (
        patch("hub.edge.agent.orchestrator.write_audit", new_callable=AsyncMock),
        patch(
            "hub.edge.agent.orchestrator.agent_tools.ask_user", new_callable=AsyncMock
        ) as mock_ask,
    ):
        mock_ask.return_value = {"status": "sent"}
        await orch.handle_command("do something weird")

    policy.evaluate.assert_called_once()
    tool_call: ToolCall = policy.evaluate.call_args[0][0]
    assert tool_call.tool == "ask_user"


@pytest.mark.asyncio
async def test_creative_intent_invalid_json_falls_back_to_summarize() -> None:
    """If LLM returns garbage, CREATIVE falls back to summarize_period."""
    orch, policy, llm, _ = make_orchestrator(
        intent_class=IntentClass.CREATIVE,
        action_class=ActionClass.AUTO,
        llm_generate_result="this is not json at all",
    )

    with (
        patch("hub.edge.agent.orchestrator.write_audit", new_callable=AsyncMock),
        patch(
            "hub.edge.agent.orchestrator.agent_tools.summarize_period", new_callable=AsyncMock
        ) as mock_summarize,
    ):
        mock_summarize.return_value = {}
        orch._session_factory = None
        await orch.handle_command("розкажи що було")

    policy.evaluate.assert_called_once()
    tool_call = policy.evaluate.call_args[0][0]
    assert tool_call.tool == "summarize_period"


# ── 7: DETERMINISTIC intent — TextResolver path ───────────────────────────────


@pytest.mark.asyncio
async def test_deterministic_no_registry_falls_back_to_ask_user() -> None:
    """With no device_registry, DETERMINISTIC must fall back to ask_user."""
    orch, policy, llm, _ = make_orchestrator(
        intent_class=IntentClass.DETERMINISTIC,
        action_class=ActionClass.AUTO,
        prototype="light_on",
    )
    # orch has no device_registry (make_orchestrator default) → _text_resolver is None

    with (
        patch("hub.edge.agent.orchestrator.write_audit", new_callable=AsyncMock),
        patch(
            "hub.edge.agent.orchestrator.agent_tools.ask_user", new_callable=AsyncMock
        ) as mock_ask,
    ):
        mock_ask.return_value = {"status": "sent"}
        await orch.handle_command("увімкни світло у вітальні")

    llm.generate.assert_not_called()
    llm.generate_constrained.assert_not_called()
    policy.evaluate.assert_called_once()
    tool_call = policy.evaluate.call_args[0][0]
    assert tool_call.tool == "ask_user"


@pytest.mark.asyncio
async def test_deterministic_with_resolver_success_publishes_mqtt() -> None:
    """TextResolver returning success → mqtt_publish tool call."""
    from hub.edge.agent.text_resolver import Resolution

    orch, policy, llm, _ = make_orchestrator(
        intent_class=IntentClass.DETERMINISTIC,
        action_class=ActionClass.AUTO,
        prototype="light_on",
    )
    # Inject a mock text_resolver
    mock_device = MagicMock()
    mock_device.mqtt_command_topic = "home/living-room/light/cmd"
    mock_device.payload_on = {"state": "ON"}
    mock_device.payload_off = None
    mock_device.actions = ["on", "off"]

    mock_resolver = AsyncMock()
    mock_resolver.resolve.return_value = Resolution(
        success=True,
        action="on",
        device=mock_device,
        params={},
    )
    orch._text_resolver = mock_resolver

    with (
        patch("hub.edge.agent.orchestrator.write_audit", new_callable=AsyncMock),
        patch(
            "hub.edge.agent.orchestrator.agent_tools.mqtt_publish", new_callable=AsyncMock
        ) as mock_pub,
    ):
        mock_pub.return_value = None
        await orch.handle_command("увімкни світло у вітальні")

    llm.generate.assert_not_called()
    policy.evaluate.assert_called_once()
    tool_call = policy.evaluate.call_args[0][0]
    assert tool_call.tool == "mqtt_publish"
    assert tool_call.topic == "home/living-room/light/cmd"


def test_speaker_room_returns_none_when_never_set() -> None:
    orch, *_ = make_orchestrator()
    assert orch._fresh_speaker_room() is None


def test_speaker_room_expires_after_ttl(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    orch, *_ = make_orchestrator()
    # Fresh: ts now, room set
    import time as _time

    from hub.edge.agent import orchestrator as orch_mod

    orch._last_identity_room = "vitalnya"
    orch._last_identity_ts = _time.monotonic()
    assert orch._fresh_speaker_room() == "vitalnya"

    # Simulate time advance beyond TTL
    monkeypatch.setattr(
        orch_mod.time, "monotonic", lambda: orch._last_identity_ts + orch_mod._IDENTITY_TTL_SEC + 1
    )
    assert orch._fresh_speaker_room() is None


@pytest.mark.asyncio
async def test_deterministic_resolver_failure_emits_info() -> None:
    """TextResolver returning failure → INFO result published, no mqtt_publish."""
    from hub.edge.agent.text_resolver import Resolution, ResolutionFailureKind

    orch, policy, llm, redis = make_orchestrator(
        intent_class=IntentClass.DETERMINISTIC,
        action_class=ActionClass.AUTO,
        prototype="light_on",
    )

    mock_resolver = AsyncMock()
    mock_resolver.resolve.return_value = Resolution(
        success=False,
        failure_kind=ResolutionFailureKind.DEVICE_NOT_FOUND,
        failure_context={"kind_ua": "світло", "room_part": " у вітальні"},
        reasoning="No light found",
    )
    orch._text_resolver = mock_resolver

    with patch("hub.edge.agent.orchestrator.write_audit", new_callable=AsyncMock) as mock_audit:
        await orch.handle_command("увімкни світло у вітальні")

    # policy.evaluate should NOT be called (failure path skips execution)
    policy.evaluate.assert_not_called()
    # Redis publish called with INFO result
    calls = [c for c in redis.publish.call_args_list if "agent:result" in str(c)]
    assert any("INFO" in str(c) for c in calls)
    # Audit row written so failure is observable in /api/agent/audit
    mock_audit.assert_awaited_once()
    audit_decision = mock_audit.await_args.args[0]
    assert audit_decision.action_class == ActionClass.INFO
    assert mock_audit.await_args.kwargs.get("executed") is False


# ── 8: CONFIRM decision → ntfy push sent, redis pubsub used ──────────────────


@pytest.mark.asyncio
async def test_confirm_decision_sends_push_and_subscribes_redis() -> None:
    """CONFIRM path must publish to Redis and call send_push."""
    orch, policy, _, redis = make_orchestrator(
        intent_class=IntentClass.UNKNOWN,
        action_class=ActionClass.CONFIRM,
    )
    policy.evaluate.return_value = Decision(
        action_class=ActionClass.CONFIRM,
        reason="test_confirm",
        confirm_message="Вимкнути охорону?",
        confirm_timeout_sec=5,
    )

    # Simulate Redis pubsub returning an empty stream (timeout → no approval)
    pubsub_mock = AsyncMock()
    pubsub_mock.get_message.return_value = None
    redis.pubsub = MagicMock(return_value=pubsub_mock)

    with (
        patch("hub.edge.agent.orchestrator.write_audit", new_callable=AsyncMock),
        patch(
            "hub.edge.agent.orchestrator.agent_tools.send_push", new_callable=AsyncMock
        ) as mock_push,
    ):
        mock_push.return_value = None
        await orch.handle_command("вимкни охорону")

    mock_push.assert_called_once()
    push_kwargs = mock_push.call_args[1]
    assert "agent-confirm" in push_kwargs.get("ntfy_url", "")
    assert redis.publish.call_count >= 1
    # confirm:request must be among the published channels
    published_channels = [call[0][0] for call in redis.publish.call_args_list]
    assert "confirm:request" in published_channels


@pytest.mark.asyncio
async def test_confirm_approved_executes_tool() -> None:
    """If confirm:result returns approved, the tool is executed."""
    orch, policy, _, redis = make_orchestrator(
        intent_class=IntentClass.UNKNOWN,
        action_class=ActionClass.CONFIRM,
    )
    import uuid as _uuid

    policy.evaluate.return_value = Decision(
        action_class=ActionClass.CONFIRM,
        reason="test_confirm",
        confirm_message="Confirm?",
        confirm_timeout_sec=5,
    )

    # We capture the confirm_id from the redis.publish call and inject it back
    published_id: list[str] = []

    async def fake_publish(channel: str, data: str) -> None:
        if channel == "confirm:request":
            msg = json.loads(data)
            published_id.append(msg["id"])

    redis.publish.side_effect = fake_publish

    call_count = 0

    async def fake_get_message(ignore_subscribe_messages: bool = True) -> dict | None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: return the approval message using captured id
            cid = published_id[0] if published_id else str(_uuid.uuid4())
            return {
                "type": "message",
                "data": json.dumps({"id": cid, "state": "approved"}),
            }
        return None

    pubsub_mock = AsyncMock()
    pubsub_mock.get_message.side_effect = fake_get_message
    redis.pubsub = MagicMock(return_value=pubsub_mock)

    with (
        patch("hub.edge.agent.orchestrator.write_audit", new_callable=AsyncMock),
        patch("hub.edge.agent.orchestrator.agent_tools.send_push", new_callable=AsyncMock),
        patch(
            "hub.edge.agent.orchestrator.agent_tools.ask_user", new_callable=AsyncMock
        ) as mock_ask,
    ):
        mock_ask.return_value = {"status": "sent"}
        await orch.handle_command("вимкни охорону")

    # Tool was executed after approval
    mock_ask.assert_called_once()


# ── Routing-branch telemetry ──────────────────────────────────────


@pytest.mark.asyncio
async def test_routing_metric_counts_unknown_branch() -> None:
    """UNKNOWN intent should increment iot_hub_agent_routing_branch_total{branch=\"unknown\"}."""
    from hub.edge.agent.orchestrator import AGENT_ROUTING

    orch, _, _, _ = make_orchestrator(
        intent_class=IntentClass.UNKNOWN, action_class=ActionClass.AUTO
    )
    before = AGENT_ROUTING.labels(branch="unknown")._value.get()

    with (
        patch("hub.edge.agent.orchestrator.write_audit", new_callable=AsyncMock),
        patch(
            "hub.edge.agent.orchestrator.agent_tools.ask_user", new_callable=AsyncMock
        ) as mock_ask,
    ):
        mock_ask.return_value = {"status": "sent"}
        await orch.handle_command("якась незрозуміла команда")

    after = AGENT_ROUTING.labels(branch="unknown")._value.get()
    assert after == before + 1, f"unknown branch counter did not increment ({before}->{after})"


@pytest.mark.asyncio
async def test_routing_metric_counts_deterministic_resolved() -> None:
    """Successful TextResolver path increments deterministic_resolved."""
    from hub.edge.agent.orchestrator import AGENT_ROUTING
    from hub.edge.agent.text_resolver import Resolution

    orch, _, _, _ = make_orchestrator(
        intent_class=IntentClass.DETERMINISTIC, action_class=ActionClass.AUTO, prototype="light_on"
    )
    mock_device = MagicMock()
    mock_device.mqtt_command_topic = "home/x/light/cmd"
    mock_device.payload_on = {"state": "ON"}
    mock_device.payload_off = None
    mock_device.mqtt_state_topic = None
    mock_device.actions = ["on", "off"]
    mock_resolver = AsyncMock()
    mock_resolver.resolve.return_value = Resolution(
        success=True, action="on", device=mock_device, params={}
    )
    orch._text_resolver = mock_resolver

    before = AGENT_ROUTING.labels(branch="deterministic_resolved")._value.get()
    with (
        patch("hub.edge.agent.orchestrator.write_audit", new_callable=AsyncMock),
        patch("hub.edge.agent.orchestrator.agent_tools.mqtt_publish", new_callable=AsyncMock),
    ):
        await orch.handle_command("увімкни світло")
    after = AGENT_ROUTING.labels(branch="deterministic_resolved")._value.get()
    assert after == before + 1
