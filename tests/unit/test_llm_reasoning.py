"""Tests for LLMReasoner (Phase 5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hub.edge.agent.llm_reasoning import LLMReasoner, Turn

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_device(
    device_id: str = "lamp_1",
    label: str = "Лампа",
    kind: str = "light",
    room_name_ua: str = "Вітальня",
    actions: list[str] | None = None,
) -> MagicMock:
    d = MagicMock()
    d.device_id = device_id
    d.label = label
    d.kind = kind
    d.room_name_ua = room_name_ua
    d.actions = actions or ["on", "off", "toggle"]
    return d


def _make_registry(devices: list[MagicMock] | None = None) -> MagicMock:
    registry = MagicMock()
    registry.all = AsyncMock(return_value=devices if devices is not None else [_make_device()])
    return registry


def _make_llm(reasoning_text: str = "Вмикаю лампу.", tool_call: dict | None = None) -> MagicMock:
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=reasoning_text)
    llm.generate_constrained = AsyncMock(
        return_value=tool_call or {"device_id": "lamp_1", "action": "on", "params": {}}
    )
    return llm


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_round_trip() -> None:
    """Happy path: LLM returns valid device_id + action → ReasonedAction.success."""
    llm = _make_llm(
        reasoning_text="Потрібно увімкнути лампу у вітальні.",
        tool_call={"device_id": "lamp_1", "action": "on", "params": {}},
    )
    registry = _make_registry()

    with patch("hub.edge.agent.grammars.load_grammar", return_value="grammar"):
        reasoner = LLMReasoner(llm=llm, registry=registry)
        result = await reasoner.reason_and_act("Увімкни лампу")

    assert result.success is True
    assert result.device_id == "lamp_1"
    assert result.action == "on"
    assert result.reasoning != ""


@pytest.mark.asyncio
async def test_reasoning_populated() -> None:
    """Reasoning text from turn 1 is stored in ReasonedAction."""
    llm = _make_llm(reasoning_text="Думаю, потрібно вимкнути лампу.")
    registry = _make_registry()

    with patch("hub.edge.agent.grammars.load_grammar", return_value="grammar"):
        reasoner = LLMReasoner(llm=llm, registry=registry)
        result = await reasoner.reason_and_act("Вимкни лампу")

    assert "лампу" in result.reasoning


@pytest.mark.asyncio
async def test_unknown_device_id_fails() -> None:
    """LLM returns device_id not in registry → ReasonedAction.success=False."""
    llm = _make_llm(tool_call={"device_id": "nonexistent_device_xyz", "action": "on", "params": {}})
    registry = _make_registry([_make_device("lamp_1", "Лампа")])

    with patch("hub.edge.agent.grammars.load_grammar", return_value="grammar"):
        reasoner = LLMReasoner(llm=llm, registry=registry)
        result = await reasoner.reason_and_act("Увімкни щось")

    assert result.success is False
    assert result.failure_reason is not None
    assert "nonexistent_device_xyz" in result.failure_reason


@pytest.mark.asyncio
async def test_fuzzy_label_match() -> None:
    """LLM hallucinates device_id but its substring matches a label → fuzzy match."""
    llm = _make_llm(tool_call={"device_id": "Лампа", "action": "off", "params": {}})
    registry = _make_registry([_make_device("lamp_real_1", "Лампа")])

    with patch("hub.edge.agent.grammars.load_grammar", return_value="grammar"):
        reasoner = LLMReasoner(llm=llm, registry=registry)
        result = await reasoner.reason_and_act("Вимкни лампу")

    assert result.success is True
    assert result.device_id == "lamp_real_1"


@pytest.mark.asyncio
async def test_generate_exception_returns_failure() -> None:
    """If generate_constrained raises, result is a graceful failure."""
    llm = MagicMock()
    llm.generate = AsyncMock(return_value="Спробую виконати.")
    llm.generate_constrained = AsyncMock(side_effect=TimeoutError("LLM timeout"))
    registry = _make_registry()

    with patch("hub.edge.agent.grammars.load_grammar", return_value="grammar"):
        reasoner = LLMReasoner(llm=llm, registry=registry)
        result = await reasoner.reason_and_act("Увімкни щось")

    assert result.success is False
    assert result.failure_reason is not None


@pytest.mark.asyncio
async def test_reasoning_failure_still_returns_result() -> None:
    """If reasoning turn fails (empty str), pipeline continues with fallback reasoning."""
    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=RuntimeError("model error"))
    llm.generate_constrained = AsyncMock(
        return_value={"device_id": "lamp_1", "action": "on", "params": {}}
    )
    registry = _make_registry()

    with patch("hub.edge.agent.grammars.load_grammar", return_value="grammar"):
        reasoner = LLMReasoner(llm=llm, registry=registry)
        result = await reasoner.reason_and_act("Увімкни лампу")

    # Should succeed since constrained turn succeeded
    assert result.success is True
    assert result.reasoning  # fallback reasoning populated


@pytest.mark.asyncio
async def test_history_passed_to_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """History turns are included in the prompt sent to the LLM."""
    captured_prompts: list[str] = []

    async def mock_generate(prompt: str, **_: object) -> str:
        captured_prompts.append(prompt)
        return "Роздуми."

    llm = MagicMock()
    llm.generate = mock_generate
    llm.generate_constrained = AsyncMock(
        return_value={"device_id": "lamp_1", "action": "on", "params": {}}
    )
    registry = _make_registry()
    history = [Turn("Вимкни телевізор"), Turn("Відкрий жалюзі")]

    with patch("hub.edge.agent.grammars.load_grammar", return_value="grammar"):
        reasoner = LLMReasoner(llm=llm, registry=registry)
        await reasoner.reason_and_act("Увімкни лампу", history)

    assert captured_prompts
    for past in ["Вимкни телевізор", "Відкрий жалюзі"]:
        assert past in captured_prompts[0]


@pytest.mark.asyncio
async def test_missing_action_fails() -> None:
    """LLM returns device_id but omits action → failure."""
    llm = _make_llm(tool_call={"device_id": "lamp_1"})  # no "action" key
    registry = _make_registry()

    with patch("hub.edge.agent.grammars.load_grammar", return_value="grammar"):
        reasoner = LLMReasoner(llm=llm, registry=registry)
        result = await reasoner.reason_and_act("Щось зроби")

    assert result.success is False


@pytest.mark.asyncio
async def test_params_forwarded() -> None:
    """Params dict from LLM is forwarded in ReasonedAction."""
    llm = _make_llm(
        tool_call={"device_id": "lamp_1", "action": "brightness_set", "params": {"brightness": 80}}
    )
    registry = _make_registry()

    with patch("hub.edge.agent.grammars.load_grammar", return_value="grammar"):
        reasoner = LLMReasoner(llm=llm, registry=registry)
        result = await reasoner.reason_and_act("Збільш яскравість до 80%")

    assert result.success is True
    assert result.params == {"brightness": 80}
    assert result.action == "brightness_set"


@pytest.mark.asyncio
async def test_empty_registry() -> None:
    """Empty device registry → device_id not found → failure."""
    llm = _make_llm(tool_call={"device_id": "lamp_1", "action": "on", "params": {}})
    registry = _make_registry(devices=[])

    with patch("hub.edge.agent.grammars.load_grammar", return_value="grammar"):
        reasoner = LLMReasoner(llm=llm, registry=registry)
        result = await reasoner.reason_and_act("Увімкни лампу")

    assert result.success is False
