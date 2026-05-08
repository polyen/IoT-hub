from __future__ import annotations

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
