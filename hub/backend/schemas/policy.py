from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class LintIssue(BaseModel):
    level: str  # "error" | "warning"
    message: str


class SimulateBody(BaseModel):
    intent_text: str
    tool: str | None = None
    payload: dict[str, Any] | None = None


class SimulateResult(BaseModel):
    matched_rule: str
    action_class: str  # "AUTO" | "CONFIRM" | "DENY"
    overrides: list[Any]
    reason: str
