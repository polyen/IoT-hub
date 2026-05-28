"""LLM-driven device control with explicit reasoning step.

Pipeline:
1. Build prompt with:
   - Full device list from registry (capped at 50).
   - Recent commands (last 5 user turns).
   - The user's text.
2. Two-turn completion:
   a. Reasoning turn (free text, max 200 tokens, temp 0.3):
      "Розглянь команду і опиши що ти збираєшся зробити одним абзацом."
   b. Tool call turn (constrained via GBNF, temp 0.0):
      "На основі попередніх роздумів видай ОДИН JSON tool call."
3. Look up device_id returned by LLM in the device registry.
4. Cache reasoning in agent:turn{type=reasoning} for UI display.

LLM failures (timeout, malformed JSON) result in a failed ReasonedAction
with failure_reason populated — caller emits explainable failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hub.edge.agent.llm_local import LocalLLMClient

logger = logging.getLogger(__name__)

_MAX_HISTORY = 5
_MAX_DEVICES = 50

# Reasoning prompt template
_REASONING_TMPL = """\
Ти — голосовий асистент розумного будинку. \
Доступні пристрої ({n} шт.):
{device_list}

Останні команди:
{history}

Команда: «{text}»

Поміркуй, що потрібно зробити. \
Відповідай однією короткою фразою українською (до 2 речень). \
Якщо команда — це сцена (наприклад «режим кіно», «романтика»), \
опиши який ОДИН пристрій найважливіше налаштувати першим.

Роздуми:"""

# Constrained turn appended to reasoning text
_TOOL_CALL_SUFFIX = """\

На основі попередніх роздумів видай ОДИН JSON tool call. \
Використовуй device_id лише зі списку вище. \
Формат: {{"device_id": "...", "action": "...", "params": {{...}}}}

JSON:"""


@dataclass
class Turn:
    text: str


@dataclass
class ReasonedAction:
    success: bool
    reasoning: str = ""
    device_id: str | None = None
    action: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    raw_tool_call: dict[str, Any] = field(default_factory=dict)
    failure_reason: str | None = None


class LLMReasoner:
    """Two-turn LLM pipeline that produces chain-of-thought then a structured tool call."""

    def __init__(
        self,
        llm: LocalLLMClient,
        registry: Any,
    ) -> None:
        self._llm = llm
        self._registry = registry

    async def reason_and_act(
        self,
        text: str,
        history: list[Turn] | None = None,
    ) -> ReasonedAction:
        """Run 2-turn pipeline. Never raises — returns failed ReasonedAction on error."""
        try:
            return await self._run(text, history or [])
        except Exception as exc:
            logger.warning("LLMReasoner failed: %s", exc, exc_info=True)
            return ReasonedAction(
                success=False,
                reasoning="",
                failure_reason=f"LLM недоступний: {exc}",
            )

    # ------------------------------------------------------------------
    # Internal pipeline
    # ------------------------------------------------------------------

    async def _run(self, text: str, history: list[Turn]) -> ReasonedAction:
        # 1. Build context
        devices = await self._registry.all()
        device_lines = self._format_devices(devices[:_MAX_DEVICES])
        history_str = self._format_history(history[-_MAX_HISTORY:])

        # 2. Turn 1 — reasoning (free text, temp 0.3)
        reasoning_prompt = _REASONING_TMPL.format(
            n=len(devices[:_MAX_DEVICES]),
            device_list=device_lines,
            history=history_str,
            text=text,
        )
        try:
            reasoning = await self._llm.generate(
                reasoning_prompt,
                max_tokens=200,
                temperature=0.3,
                stop=["\n\n", "JSON:", "На основі"],
            )
            reasoning = reasoning.strip()
        except Exception as exc:
            logger.warning("LLMReasoner: reasoning turn failed: %s", exc)
            reasoning = ""

        if not reasoning:
            reasoning = "Виконую команду."

        # 3. Turn 2 — constrained JSON (GBNF, temp 0.0)
        from hub.edge.agent.grammars import load_grammar  # noqa: PLC0415

        grammar = load_grammar("structured_tool_call")
        tool_prompt = reasoning_prompt + reasoning + _TOOL_CALL_SUFFIX

        try:
            raw = await self._llm.generate_constrained(
                tool_prompt,
                grammar,
                max_tokens=150,
            )
        except Exception as exc:
            logger.warning("LLMReasoner: constrained turn failed: %s", exc)
            return ReasonedAction(
                success=False,
                reasoning=reasoning,
                failure_reason=f"LLM не згенерував tool call: {exc}",
            )

        device_id = str(raw.get("device_id", "")).strip()
        action = str(raw.get("action", "")).strip()
        params: dict[str, Any] = raw.get("params", {})
        if not isinstance(params, dict):
            params = {}

        if not device_id or not action:
            return ReasonedAction(
                success=False,
                reasoning=reasoning,
                failure_reason="LLM не вказав device_id або action",
                raw_tool_call=raw,
            )

        # 4. Validate device_id against registry
        device = next((d for d in devices if d.device_id == device_id), None)
        if device is None:
            # LLM hallucinated a device_id — find closest match by label/alias
            device = next(
                (d for d in devices if device_id.lower() in (d.label or "").lower()),
                None,
            )
            if device is None:
                return ReasonedAction(
                    success=False,
                    reasoning=reasoning,
                    failure_reason=f"Пристрій «{device_id}» не знайдено в реєстрі",
                    raw_tool_call=raw,
                )
            logger.info("LLMReasoner: fuzzy-matched %r → %r", device_id, device.device_id)
            device_id = device.device_id

        return ReasonedAction(
            success=True,
            reasoning=reasoning,
            device_id=device_id,
            action=action,
            params=params,
            raw_tool_call=raw,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_devices(devices: list[Any]) -> str:
        if not devices:
            return "  (список порожній)"
        lines: list[str] = []
        for d in devices:
            label = d.label or d.device_id
            lines.append(
                f"  • id={d.device_id!r} label={label!r} kind={d.kind!r} room={d.room_name_ua!r} actions={d.actions!r}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_history(turns: list[Turn]) -> str:
        if not turns:
            return "  (немає)"
        return "\n".join(f"  - {t.text}" for t in turns)
